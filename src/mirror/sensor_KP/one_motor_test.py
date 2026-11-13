
import time
from datetime import datetime
import asyncio
from typing import Optional, List
# from mirror.sensor_KP.sensor import SensorReader   # 需要替换成国产
# from mirror.amplifier.domestic_amplifier import Amplifier
from mirror.pmac_controller import PMAC_Controller, SSH_Config
import csv
from mirror.sensor_KP.plot import DataAnalyzer
from mirror.logger import setup_logger
import signal
import aiofiles
from dataclasses import dataclass
from bidict import bidict
import pandas as pd
from functools import partial
import os
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import re

# 自动计算进程池大小，优先用逻辑核的1/3，最小1个，最大不超过8（避免占满CPU影响实时控制）
LOGIC_CPU_COUNT = os.cpu_count() or 4
MAX_WORKERS = min(8, max(1, LOGIC_CPU_COUNT // 3))
# Linux下强制用spawn启动模式，避免fork继承父进程全局状态导致串图
mp_context = mp.get_context("spawn")

# 子进程初始化：每个进程启动时自动配置matplotlib，避免重复设置
def _worker_init():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei']
    matplotlib.rcParams['axes.unicode_minus'] = False

# 全局进程池实例，整个程序复用
GLOBAL_PLOT_POOL = ProcessPoolExecutor(
    max_workers=MAX_WORKERS,
    mp_context=mp_context,
    initializer=_worker_init
)

# 写到文件顶层，OneMotorTest类外面，全局可导入
def run_plot_task(filepath: str, x_col: str, y_col: str, one_actuator_info: Optional[dict] = None, is_linearity_test: Optional[bool] = True):
    """独立绘图任务，进程池可序列化，内部处理异常"""
    try:
        DataAnalyzer(filepath).plot(x_col, y_col, one_actuator_info, is_linearity_test)
        return one_actuator_info
    except Exception as e:
        import logging
        logging.warning(f"绘图失败 {filepath}: {str(e)}")
        return None


class OneMotorTest:
    def __init__(self, df: Optional[pd.DataFrame], pmac, amplifier, one_actuator_info: Optional[dict] = None, is_linearity_test: Optional[bool] = True):
        self.logger = setup_logger()
        self.motor_id = one_actuator_info["电机id"]
        self._stop_event = asyncio.Event()
        self.sensor_id = one_actuator_info["channel_id"]
        self.data_pairs = []                                   # 存储步数和力值的列表
        self.amplifier = amplifier
        self.channel_vals: List[Optional[float]] = [None] * 8  # 用列表存8个通道值，替代冗余变量
        self.val:Optional[float] = None                        # 当前测试电机对应的传感器通道的值
        self.pmac = pmac
        self.sensor_index = one_actuator_info["sensor_index"]  # 表示传感器在全部150个传感器中的索引位置，0~149
        self.mirror_id = one_actuator_info["mirror_id"]
        self.one_actuator_info = one_actuator_info
        self.is_linearity_test = is_linearity_test
        self.test_threshold = list(map(float, re.findall(r'-?\d+\.?\d*', one_actuator_info["测试区间"])))

    async def save_to_csv(self):
        if self.data_pairs:
            filename = f"mirror{self.mirror_id}_data/motor{self.motor_id}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
            async with aiofiles.open(filename, 'w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                # 设置表头
                await file.write('序号,Steps,Force_Value\n')
                # 写入所有数据行
                for i, (step, force) in enumerate(self.data_pairs, 1):
                    await file.write(f"{i},{step},{force}\n")
            self.logger.info(f"\n✅ 所有数据已保存到: {filename}")
            self.logger.info(f"📊 共保存 {len(self.data_pairs)} 行数据")
            # 绘图逻辑如果是同步的，放在线程池里跑避免阻塞,替换原来的同步调用，绘图放到线程池执行
            loop = asyncio.get_running_loop()
            try:
                # 整个绘图逻辑（实例化+调用）都在进程池内执行，完全隔离，不会阻塞主线程
                plot_task = partial(
                    run_plot_task,
                    filename,
                    'Force_Value',
                    'Steps',
                    self.one_actuator_info,
                    self.is_linearity_test
                )
                updated_actuator_info = await loop.run_in_executor(GLOBAL_PLOT_POOL, plot_task)
                if updated_actuator_info:
                    print("self.one_actuator_info有更新！")
                    self.one_actuator_info.update(updated_actuator_info)
            except Exception as e:
                self.logger.warning(f"⚠️ 绘图失败，不影响测试结果: {str(e)}")
        else:
            self.logger.error("❌ 没有数据需要保存")
        return self.data_pairs

    async def get_sensor_data(self):
        loop = asyncio.get_running_loop() # 新增：获取当前事件循环
        while not self._stop_event.is_set():
            try:
                try:
                    start_time = time.perf_counter()
                    res = await self.amplifier.read_data()
                    elapsed_time = time.perf_counter() - start_time
                    print(f"✅️放大器读取数据成功, 实际耗时: {elapsed_time:.4f} 秒")
                except Exception as e:
                    self.logger.error(f"Sensor-reading error: {str(e)}")
                    break
                timestamp = res[1].strftime("%Y-%m-%dT%H:%M:%S.%f")
                sensor_values = res[2]
                if not sensor_values:
                    self.logger.error(f"Sensor reading info is null")
                    continue

                self.channel_vals = sensor_values             # 更新通道值列表
                self.val = sensor_values[self.sensor_id - 1]  # 获取对应电机的传感器通道值

                print(f"[{timestamp[:-5]}]: chan{self.sensor_id}_val={self.val}\n")
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f"Sensor reading error: {str(e)}")

    async def safety_monitor(self):
        """安全监控任务"""
        while not self._stop_event.is_set():
            if self.val is not None and not (self.test_threshold[0] <= self.val <= self.test_threshold[1]):  # 测试的传感器安全量程范围
                self.logger.warning("⚠️  self.val out of bounds! Stopping system.")
                self._stop_event.set()  # 设置停止信号
                break
            await asyncio.sleep(0.5) # 0.1s轮询，不占CPU
    
    def signal_handler(self, loop):
        self.logger.info("\n接收到关闭信号, 停止当前电机, 正在退出...")
        self._stop_event.set()  # 设置停止信号
       
    
    async def run_test(self, data_list):
        sensor_task = None
        safety_task = None
        self.one_actuator_info["脉冲范围"] = f"[start={data_list[0]}, stop={data_list[-1]}, step=f'{data_list[0]}- {data_list[1]}']"

        # 设置信号处理
        loop = asyncio.get_running_loop()
        for sig in [signal.SIGINT, signal.SIGTERM]:  # Ctrl+C 和 kill 信号
            loop.add_signal_handler(sig, self.signal_handler, loop)

        try:
                await self.pmac.connect()
                try:
                    sensor_task = asyncio.create_task(self.get_sensor_data())
                    safety_task = asyncio.create_task(self.safety_monitor())
                    await self.pmac.exec_command(f"#{self.motor_id}J/")            # 使能电机

                    for step in data_list:
                        if self._stop_event.is_set():
                            self.logger.info("🛑 停止信号已触发，提前终止测试循环")
                            break
                        try:
                            await self.pmac.exec_command(f"#{self.motor_id}J={step}")
                            await asyncio.sleep(3)  # 等待数据稳定
                            if self._stop_event.is_set():
                                self.logger.info("🛑 安全监控触发，提前终止等待")
                                break
                            if self.val is not None:
                                self.data_pairs.append((step, self.val))
                                self.logger.info(f"✅ 记录: 步数={step}, 力值={self.val:.3f}")
                            else:
                                self.logger.warning("⚠️ 传感器数据为空")
                        except Exception as e:
                                self.logger.error(f"Error executing command: {e}")
                                break
                except Exception as e:
                    self.logger.error(f"Error: {e}")
        except Exception as e:
            self.logger.error(f"💥 系统错误: {e}")
        finally:
            # 清理
            self._stop_event.set()

            # 安全停止任务
            tasks = []
            if sensor_task: tasks.append(sensor_task)
            if safety_task: tasks.append(safety_task)
            if tasks:
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                except Exception as e:
                    self.logger.warning(f"⚠️ 等待任务退出异常: {str(e)}")

            # 停止电机
            try:
                if self.pmac and self.pmac.is_connected:
                    await self.pmac.exec_command(f"#{self.motor_id}k")
                    self.logger.info(f"✅ 电机{self.motor_id}已成功去使能")
                else:
                    # 连接已断开的情况下尝试重连停电机
                    self.logger.warning(f"PMAC连接已断开, 尝试重连停电机{self.motor_id}")
                    async with PMAC_Controller(SSH_Config(host = "192.168.0.200")) as pmac_reconnect:
                        await pmac_reconnect.connect()
                        await pmac_reconnect.exec_command(f"#{self.motor_id}k")
                        self.logger.info(f"✅ 电机{self.motor_id}重连后，去使能成功")
            except Exception as e:
                self.logger.critical(f"❌ 电机停止失败！请立刻手动断电！错误: {str(e)}")

        await self.save_to_csv()
