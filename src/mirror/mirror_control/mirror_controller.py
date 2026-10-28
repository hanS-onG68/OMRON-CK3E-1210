"""子镜闭环控制模块"""

import asyncio, time, atexit
from datetime import datetime
from multiprocessing import Process, Event
from multiprocessing.shared_memory import SharedMemory
from dataclasses import dataclass
import numpy as np
import pandas as pd
from typing import Optional
from importlib import resources

from mirror.mirror_control.config import (
        MIRRORS_COUNT, ACTUATORS_PER_MIRROR, SENSORS_PER_AMP, CAPACITY_SENSORS,
        TOTAL_BUFF_BYTES, SHM_NAME,
        DEFAULT_CTRL_IPS, DEFAULT_AMP_PORTS,
        MOTOR_STEPS_LIMIT, FORCE_100_UPPER, FORCE_100_LOWER, FORCE_200_UPPER, FORCE_200_LOWER, 
        FORCE_TIMEOUT,
)
# from mirror.mirror_control.controller import Controller
from mirror.pmac_controller import PMAC_Controller, SSH_Config
from mirror.mirror_control.sensor_collector import collector
from mirror.mirror_control.utils import OCS_Logger
from collections import deque


class MirrorsController:
    """子镜面形闭环控制类"""
    Target:Optional[np.ndarray] = None       # 力维持目标值，由上层提供(主动光学/查表)
    def __init__(self, debug=False, is_domestic:bool = True):
        self.debug = debug
        self.logger = OCS_Logger(name="MIRRORS", debug=self.debug)
        self.is_domestic = is_domestic       # 区分是国产的放大器还是进口的放大器，True表示国产，False表示进口
        self._cmds_history = deque()  # 存储（timestamp，is_empty）
        self._cmds_window = 60.0      # 60秒的时间窗口
        self._motors_status_history = deque()  # 存储（timestamp)

        # 控制标签
        self.stop_event = Event()
        self.close_loop = False

        # 闭环矩阵
        target_file = resources.files("mirror.mirror_control").joinpath("settings/Initial_Target.csv") 
        target_data = np.loadtxt(target_file, delimiter=',', dtype=str, skiprows=1, comments='#')
        initial_target_force = np.char.strip(target_data[:, 1]).astype(float)
        MirrorsController.Target = initial_target_force.reshape(MIRRORS_COUNT, ACTUATORS_PER_MIRROR)    # 物理位置的力维持目标值，由上层提供(主动光学/查表)
        self.Force = np.zeros((MIRRORS_COUNT, ACTUATORS_PER_MIRROR))        # 力传感器数据
        self.Force_TS = np.zeros((MIRRORS_COUNT, ACTUATORS_PER_MIRROR))     # 力传感器时间戳
        self.Error = np.zeros((MIRRORS_COUNT, ACTUATORS_PER_MIRROR))        # 目标值与实际值之间的偏差

        kp_file = resources.files("mirror.mirror_control").joinpath("settings/Coefficient_K_p.csv")
        self.K_p = np.loadtxt(kp_file, delimiter=',', skiprows=1)    # 增益

        threshold_file = resources.files("mirror.mirror_control").joinpath("settings/Threshold.csv")
        self.Threshold = np.loadtxt(threshold_file, delimiter=',', skiprows=1)              # 阈值：对应位置的传感器不超过该位置的阈值时，不需要动

        cols = np.arange(25).reshape(1, -1)
        multiplier = np.where((cols % 2 == 0) & (cols != 0), 100.0, 20.0)   # 内圈100(偶数列)，外圈20(奇数列)，第0列为20(奇数列)
        self.Force_limit_pos = np.ones((MIRRORS_COUNT, ACTUATORS_PER_MIRROR)) * multiplier         # 力值上限
        self.Force_limit_neg = -np.ones((MIRRORS_COUNT, ACTUATORS_PER_MIRROR))* multiplier         # 力值下限
        self.Force_timeout = FORCE_TIMEOUT

        self.Steps = np.zeros_like(MirrorsController.Target)
        self.steps_limit = MOTOR_STEPS_LIMIT

        self.Available = np.full((MIRRORS_COUNT, ACTUATORS_PER_MIRROR), False, dtype=bool)  # 该位置是否可用

        self.motors_status = np.full((MIRRORS_COUNT, ACTUATORS_PER_MIRROR), True, dtype=bool)  # 该位置电机是否正常: true表示正常，false表示异常(默认都正常)
        
        # 调试时选择：
        self.Available[2, :] = True    # 选择几号边缘子镜


        # 映射索引
        mapping_file = resources.files("mirror.mirror_control").joinpath("settings/Actuator_Mapping.csv")
        mapping_data = np.loadtxt(mapping_file, delimiter=',', skiprows=1, dtype=str)
        self.mapping = np.char.strip(mapping_data[:,:]).astype(int)
        self.actuator_id, self.controller_id, self.axis_id, self.amplifer_id, self.channel_id = self.mapping.T    # 配置表拆成5个列向量
        self._sensor_idx = self.amplifer_id*SENSORS_PER_AMP + self.channel_id
        self._axis_idx = self.axis_id.reshape(MIRRORS_COUNT, ACTUATORS_PER_MIRROR)                                # 逻辑促动器空间到物理传感器空间的映射（1*150）

        # 共享内存
        self.shm = self._create_shm()
        self._buffer = np.ndarray((CAPACITY_SENSORS*2,), dtype=np.float64, buffer=self.shm.buf)
        self._buffer.fill(0.0)                              # 初始化共享内存数据区，避免初始全0导致的误动
        self._data =  self._buffer[:CAPACITY_SENSORS]       # 传感器数据视图，零拷贝
        self._timestamp = self._buffer[CAPACITY_SENSORS:]   # 传感器时间戳视图，零拷贝

        # 控制器
        self._create_controllers()

        # 传感器
        self._create_amplifiers()

        self.change_va = self.Force - MirrorsController.Target  # 当前值和目标值的差值

    async def disable_all_motor(self):
        enable_actuators = np.argwhere(self.motors_status)
        for actuator_info in enable_actuators:
            ctrl_id, actuator_id = actuator_info
            motor_id = self._axis_idx[ctrl_id, actuator_id]
            if ctrl_id in self.Controllers and self.Controllers[ctrl_id] and self.Controllers[ctrl_id].is_connected:
                await self.Controllers[ctrl_id].exec_command(f"#{motor_id}k")  # 该位置电机去使能
        self.logger.info(f"[资源清理阶段]所有电机完成去使能")
        
    async def close(self):
        await self.disable_all_motor()  # 所有可用的电机去使能
        if self.stop_event and not self.stop_event.is_set():
            self.stop_event.set()       # 发信号，让采集子进程自己停止
        for worker in self.Amplifiers.values():
            worker.join(timeout=2.0)    # 回收采集子进程
        self.Amplifiers.clear()
        tasks = [ctrl.disconnect() for ctrl in self.Controllers.values()]
        await asyncio.gather(*tasks)
        self.Controllers.clear()

        if hasattr(self, '_buffer'):    # 回收共享内存引用
            del self._data
            del self._timestamp
            del self._buffer
        if self.shm:
            self.shm.close()
            self.shm.unlink()           # 回收共享内存
            self.shm = None

    def _create_shm(self) -> SharedMemory:
        # created shared memory in /dev/shm/
        shm = SharedMemory(create=True, name=SHM_NAME, size=TOTAL_BUFF_BYTES)
        atexit.register(self._clean_shm)
        self.logger.info(f"Created shared memory '{SHM_NAME}'")
        return shm

    def _clean_shm(self) -> None:
        try:
            SharedMemory(name=SHM_NAME).unlink()
        except FileNotFoundError:
            return
        except Exception as err:
            self.logger.warning(f"Failed to unlink old shared memory '{SHM_NAME}': {err}!r")

    def _create_controllers(self):
        ip_file = resources.files("mirror.mirror_control").joinpath("settings/Controller_IP.csv")
        self.controller_ips = self._load_hardware_config(ip_file)
        self.Controllers = dict()
        for ctrl_id in np.unique(self.controller_id[self.Available.ravel()]):
            ctrl_ip = self.controller_ips[ctrl_id]
            config = SSH_Config(ctrl_ip)
            controller = PMAC_Controller(config)
            asyncio.get_event_loop().create_task(controller.connect())
            self.Controllers[ctrl_id] = controller
            self.logger.info(f"Controllers[{ctrl_id}] is CONNECTED to {ctrl_ip}")

    def _create_amplifiers(self):
        def run_collector(amp_info:str, amp_id:int, shm_name:str, stop_event:Event, start_time:float, *, interval:float=2.0, data_rate:float=1.0, debug=False, is_domestic:bool = True):
            asyncio.run(collector(amp_info, amp_id, shm_name, stop_event, start_time, interval=1.0, data_rate=1.0, debug=debug, is_domestic=is_domestic))
        amp_file = resources.files("mirror.mirror_control").joinpath("settings/Domestic_Amplifier_Mapping.csv") if self.is_domestic else resources.files("mirror.mirror_control").joinpath("settings/Imported_Amplifier_Mapping.csv")
        self.amplifers = self._load_hardware_config(amp_file)
        self.start_time = time.monotonic()
        self.Amplifiers = dict()
        for amp_id in np.unique(self.amplifer_id[self.Available.ravel()]):
            amp_info = self.amplifers[amp_id]   # 国产：amp_info表示ip，进口：amp_info表示串口号（形如：/dev/ttry00）
            worker = Process(    # 放大器：单进程设备
                target=run_collector,
                args=(amp_info, amp_id, SHM_NAME, self.stop_event, self.start_time,),
                kwargs={'interval':1.0, 'data_rate':1.0, 'debug':self.debug, 'is_domestic':self.is_domestic},
            )
            worker.start()
            self.Amplifiers[amp_id] = worker
            self.logger.info(f"Amplifiers[{amp_id}] is CONNECTED to {amp_info}")
    
    
    @staticmethod
    def _load_hardware_config(filepath):
        data = np.loadtxt(filepath, delimiter=',', dtype=str, skiprows=1, comments='#')
        dev_id = np.char.strip(data[:, 0]).astype(int)
        dev_ip = np.char.strip(data[:, 1])                  # 只去空格，不转类型
        dev_data = np.zeros(dev_id.max() + 1, dtype='<U15')
        dev_data[dev_id.astype(int)] = dev_ip
        return dev_data.tolist()


    def get_force(self):
        """获取最新力传感器数据和时间戳 -- 花式索引, 一次拷贝, 1.17us"""
        self.Force = self._data[self._sensor_idx].reshape(MIRRORS_COUNT, ACTUATORS_PER_MIRROR)
        self.Force_TS = self._timestamp[self._sensor_idx].reshape(MIRRORS_COUNT, ACTUATORS_PER_MIRROR)
        self.change_va = self.Force - MirrorsController.Target  # 当前值和目标值的差值
        return self.Force, self.Force_TS

    async def run(self):
        try:
            while True:
                await asyncio.sleep(5.0)  # 每5秒钟执行一次闭环控制
                now_ts = time.time()

                # 每一个小时刷新一下motor的状态，避免长时间运行后出现异常
                await self.reset_motor_status(now_ts)

                # # 获取传感器最新数据
                self.Force, self.Force_TS = self.get_force()
                
                 # 全矩阵运算，效率不高，但意思清晰。如果要追求效率，可以先用条件卡住矩阵
                Error = MirrorsController.Target - self.Force
                raw = Error * self.K_p
                 
                 # # 严格条件筛选
                valid_mask = (self.Available & self.motors_status &
                         ~np.isnan(self.Force) &
                         (MirrorsController.Target>self.Force_limit_neg) &
                         (MirrorsController.Target<self.Force_limit_pos) &
                         (self.Force_TS - now_ts < self.Force_timeout)
                 )
                need_move_mask = valid_mask & (np.abs(Error) > self.Threshold)
                
                 # # 转换成各促动器电机补偿步数
                self.Steps = np.where(need_move_mask, np.clip(raw, -self.steps_limit, self.steps_limit), 0.0)
                    
                cmds = self.build_motor_commands()  # 获取各个电机需要执行的命令

                await self.check_motors_status(now_ts, cmds)  # 检查电机状态，记录历史命令
                
                if cmds:
                    await self.execute_commands(cmds)  # 控制电机运行
                with pd.option_context('display.max_rows', 6, 'display.max_columns', 25, 'display.precision', 2):
                    self.logger.info(f"Force:\n{pd.DataFrame(self.Force)}\n")
                    self.logger.info(f"ERROR:\n{pd.DataFrame(Error)}\n")
                    self.logger.info(f"raw_Steps:\n{pd.DataFrame(raw)}\n")
                    self.logger.info(f"Steps:\n{pd.DataFrame(self.Steps)}\n")
                    self.logger.info(f"CMDS:\n{cmds}\n")
        except KeyboardInterrupt:
            pass

    def build_motor_commands(self):
        commands = dict()
        for ctrl_id in range(MIRRORS_COUNT):
            non_pos = np.nonzero(self.Steps[ctrl_id])[0]  # 返回当前子镜中step不为0的索引 
            axes = self._axis_idx[ctrl_id][non_pos]
            if len(axes) == 0:
                continue
            parts = [f"#{axis_id:02d}J:{int(self.Steps[ctrl_id, non_pos[ind]])}" for ind, axis_id in enumerate(axes)]
            cmd = " ".join(parts)  # 一个子镜需要动的促动器的命令的集合
            commands[ctrl_id] = cmd
        return commands

    async def execute_commands(self, commands):
        if not commands:
            return
        tasks = []
        for ctrl_id, cmd in commands.items():
            tasks.append(asyncio.create_task(self.Controllers[ctrl_id].exec_command(cmd)))
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)

    async def check_motors_status(self, now_ts, cmds):
        self._cmds_history.append((now_ts, cmds, self.Steps.copy()))     # 记录当前时间戳和是否有命令

        if now_ts - self._cmds_history[0][0] < self._cmds_window:  # 每隔一分钟检查一次
            return
        
        # 移除过期的历史记录
        while self._cmds_history and self._cmds_history[0][0] < now_ts - self._cmds_window:
            self._cmds_history.popleft()
                        
        empty_cmds_records = [ts for ts, cmds, _ in self._cmds_history if len(cmds) == 0]  # 记录过去60秒内没有电机命令的时间戳
        if empty_cmds_records:
            self.logger.info(f"检测到过去{self._cmds_window}秒内有{len(empty_cmds_records)}次无电机命令的情况: 电机无异常!")
        else:
            steps_stack = np.stack([steps for ts, cmds, steps in self._cmds_history], axis=0)
            alwayes_nonzero_mask = np.all(steps_stack != 0, axis=0)  # 检查每个位置在过去60秒内是否一直有非零步数(6x25: true表示一直有非零步数，false表示有过零步数)
            nonzero_positions = np.argwhere(alwayes_nonzero_mask)    # 获取一直有非零步数的位置索引
            for ctrl_id, actuator_id in nonzero_positions:
                motor_id = self._axis_idx[ctrl_id, actuator_id]
                self.logger.error(f"❌️检测到过去{self._cmds_window}秒内, 位置(子镜{ctrl_id}-促动器{actuator_id}-电机{motor_id})一直有非零步数, 可能电机异常!")
                if self.Controllers[ctrl_id] and self.Controllers[ctrl_id].is_connected:
                    await self.Controllers[ctrl_id].exec_command(f"#{motor_id}k")  # 该位置电机去使能
                self.motors_status[ctrl_id, actuator_id] = False                   # 标记该位置电机异常

    async def reset_motor_status(self, now_ts):
        if not self._motors_status_history:
            self._motors_status_history.append(now_ts)
            return
        if now_ts - self._motors_status_history[-1] < self._cmds_window * 60:  # 每隔一小时检查一次电机状态
            return
        self.motors_status.fill(True)  # 重置电机状态为正常
        self._motors_status_history.popleft()
        self._motors_status_history.append(now_ts)
        self.logger.info(f"✅️电机状态已重置为正常, 时间戳: {now_ts}")
        


if __name__ == "__main__":
    mirrors = MirrorsController(is_domestic=False)
    def _atexit_cleanup():
        asyncio.run(mirrors.close())
    atexit.register(_atexit_cleanup)
    try:
        asyncio.run(mirrors.run())
    except (Exception, KeyboardInterrupt):
        pass
