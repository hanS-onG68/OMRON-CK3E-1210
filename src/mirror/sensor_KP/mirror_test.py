
import time
from datetime import datetime
import asyncio
from typing import Optional
# from mirror.sensor_KP.sensor import SensorReader
from mirror.amplifier.domestic_amplifier import Amplifier as DomesticAmplifier
from mirror.amplifier.imported_amplifier import Amplifier as ImportedAmplifier
from mirror.pmac_controller import PMAC_Controller, SSH_Config
import csv
from mirror.logger import setup_logger
import signal
import aiofiles
from dataclasses import dataclass
from bidict import bidict
import pathlib
from mirror.sensor_KP.one_motor_test import OneMotorTest
import pandas as pd
import inspect
from mirror.sensor_KP.one_motor_test import GLOBAL_PLOT_POOL
from mirror.sensor_KP.plot import kp
import ipaddress
from pathlib import Path
import json,os
from mirror.sensor_KP.excel_generator import ExcelDataHandler

# 6个子镜对应6个pmac控制器
config0 = SSH_Config()   # host = "192.168.0.200"
pmac_controler0 = PMAC_Controller(config0)

# config1 = SSH_Config(host = "192.168.0.201")
# pmac_controler1 = PMAC_Controller(config1)

# config2 = SSH_Config(host = "192.168.0.202")
# pmac_controler2 = PMAC_Controller(config2)

# config3 = SSH_Config(host = "192.168.0.203")
# pmac_controler3 = PMAC_Controller(config3)

# config4 = SSH_Config(host = "192.168.0.204")
# pmac_controler4 = PMAC_Controller(config4)

# config5 = SSH_Config(host = "192.168.0.205")
# pmac_controler5 = PMAC_Controller(config5)


Id2Controler = {
    "192.168.0.200": pmac_controler0,
    # "192.168.0.201": pmac_controler1,
    # "192.168.0.202": pmac_controler2,
    # "192.168.0.203": pmac_controler3,
    # "192.168.0.204": pmac_controler4,
    # "192.168.0.205": pmac_controler5
}

class MirrorsTest:
    def __init__(self, is_domestic:bool = True, mirror_id:int = 1, is_linearity_test:bool = True):
       self.logger = setup_logger()
       self.is_domestic = is_domestic         #  区分是国产的放大器还是进口的放大器，True表示国产，False表示进口
       self.mirror_id = mirror_id
       self.is_linearity_test = is_linearity_test
       self.json_file_path = f"mirror{self.mirror_id}.json"
       self.all_actuator_info = {}  # 用于存储所有促动器（电机+传感器+弹簧）的测试信息，方便后续打印和记录
       try:
           from importlib import resources
           file = resources.files("mirror.sensor_KP").joinpath("test_config.csv")
           self.df = pd.read_csv(
               file, 
               sep=',',
               comment='#',           # 自动跳过所有#开头的行
               skip_blank_lines=True, # 自动跳过所有空行
               dtype={                # 强制所有IP列读取为字符串，避免类型自动转换
                    "Pmac_Controller_ip": str,
                    "Amplifier_ip": str,
                    "Sping_range": str,
               }
           )
       except Exception as e:
           self.logger.error(f"read_csv error: {str(e)}")
           return
       pass
    
    def get_imported_amplifier_info(self):  # 进口放大器
        ttyr_list = []
        try:
            path = pathlib.Path("/dev/").iterdir()
            ttyr_list = sorted([f"{device}" for device in path if device.name.startswith("ttyr")])
            self.logger.info(f"Found {len(ttyr_list)} ttyr devices:")
            self.logger.info(*[item for item in ttyr_list])
            return ttyr_list
        except Exception as e:
            self.logger.error(f"{inspect.currentframe().f_code.co_name} 出现异常, Error: {str(e)}")
            return []
    def get_domestic_amplifier_info(self):  # 国产放大器
        dev_info = []
        start_ip = "192.168.0."  # 起始ip
        for id in range(111, 112, 1): # 全部需要19个放大器，测试时可根据需要收放
            current_ip = start_ip + str(id)
            dev_info.append(current_ip)
        return dev_info
    
    async def get_sensor_reader(self, amp_ip: str):
        if self.is_domestic:
            sensor_reader = DomesticAmplifier(amp_ip)      # 国产放大器
            await sensor_reader.connect()
            return sensor_reader
        sensor_reader = ImportedAmplifier(amp_ip, 3, 1.0)   # 进口放大器
        return sensor_reader

    def save_all_test_result_to_json(self):
        """
        把全局所有促动器测试结果导出为JSON文件, 自动过滤不可序列化的硬件对象
        """
        # 1. 如果文件不存在，自动创建全新的空文件
        if not os.path.exists(self.json_file_path):
            with open(self.json_file_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=4)

        # 2. 先读取文件中所有的历史旧数据
        with open(self.json_file_path, "r", encoding="utf-8") as f:
            old_exist_data = json.load(f)

        # 3. 过滤掉不可序列化的硬件对象，得到干净的可写入数据
        clean_new_data = {}
        for unique_key, actuator_data in self.all_actuator_info.items():
            clean_item = {}
            for k, v in actuator_data.items():
                # 只保留JSON支持的基础数据类型，排除pmac控制器、传感器实例这类硬件对象
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    clean_item[k] = v
            clean_new_data[unique_key] = clean_item

        # 4. 新旧数据合并，新数据的key如果和旧数据重复，会自动覆盖旧数据（适配重测场景）
        merged_all_data = {**old_exist_data, **clean_new_data}
        
        # 5. 整体把合并后的完整数据写回文件，完全不覆盖历史旧条目
        with open(self.json_file_path, "w", encoding="utf-8") as f:
            json.dump(merged_all_data, f, ensure_ascii=False, indent=4)

        self.logger.info(f"✅ 所有测试结果已成功导出到JSON文件: {self.json_file_path}")
        return self.json_file_path

    async def one_amplifier_test(self, amp_ip):
        async def _wrap_motor_test(motor_test):    # 给单个电机任务包异常捕获，异常只影响自己
            try:
                data_list = list(range(0, -240000, -5000))

                await motor_test.run_test(
                    data_list
                )
            except Exception as e:
                self.logger.error(f"❌ 电机{motor_test.motor_id}测试失败: {str(e)}，已安全停转")
            return motor_test.one_actuator_info
    
        sensor_reader = await self.get_sensor_reader(amp_ip)
        try:
            matched_ip = self.df[(self.df['Amplifier_ip'] == amp_ip) & (self.df['mirror_id'] == self.mirror_id)]
            if matched_ip.empty:
                self.logger.error(f"❌ 放大器{amp_ip}未找到匹配记录，跳过当前放大器所有测试")
                return # 仅跳过当前放大器，不影响其他放大器
            amp_id = matched_ip['Amplifier_id'].values[0]
            PmacControler_ip = matched_ip['Pmac_Controler_ip'].values[0]
            pmac_controller = Id2Controler[PmacControler_ip]
            motor_id = matched_ip['Motor_id'].values
            self.logger.info(f"PmacControler_ip = {PmacControler_ip}, amp_id = {amp_id}, motor_id = {motor_id}")
        except Exception as e:
            self.logger.error(f"❌ 放大器{amp_ip}初始化失败: {str(e)}，跳过当前放大器")
            return # 仅跳过当前放大器

        try:
            tasks = []
            cmds = []
            for chan_id in range(8, 9):   # 每个放大器有8个通道，测试每个通道对应的电机
                try:
                    matched_chan = matched_ip[matched_ip['Channel_id'] == chan_id]
                    if matched_chan.empty:
                        self.logger.warning(f"⚠️ 放大器{amp_ip}通道{chan_id}未找到匹配记录，跳过当前通道测试")
                        continue
                except Exception as e:
                    self.logger.error(f"❌ 放大器{amp_ip}通道{chan_id}初始化失败: {str(e)}，跳过当前通道")
                    continue
                if matched_chan['Motor_id'].values[0] == -1:  # 放大器通道未连接电机
                    self.logger.warning(f"⚠️ 放大器{amp_ip}通道{chan_id}未连接电机，跳过当前通道测试")
                    continue
                one_actuator_info = {"传感器id": amp_ip + f": {str(chan_id)}", 
                                     "pmac_ip": PmacControler_ip, 
                                     "测试区间": None,
                                     "测试时间": None,
                                     "传感器量程": matched_chan['Sensor_range'].values[0],
                                     "脉冲范围": None,
                                     "电机id": matched_chan['Motor_id'].values[0],
                                     "弹簧id": matched_chan['Spring_id'].values[0],
                                     "amplifier_id": amp_id,
                                     "channel_id": chan_id,
                                     "mirror_id": self.mirror_id,
                                     "sensor_index": amp_id * 8 + chan_id - 1,   # 表示传感器在全部150个传感器中的索引位置，0~149
                                     "拟合图名称": None,
                                     "拟合图": None,
                                     "线性方程": None,
                                     "线性度": None
                }
                one_actuator_info["测试区间"] = "[-150N, 150N]" if one_actuator_info["传感器量程"] == "200N" else "[-60N, 60N]"
                self.logger.info(f"one_actuator_info = {one_actuator_info}")
                motor = OneMotorTest(df=self.df, pmac=pmac_controller, amplifier=sensor_reader, one_actuator_info=one_actuator_info, is_linearity_test=self.is_linearity_test)    # 测试传感器对应的电机
                task = asyncio.create_task(_wrap_motor_test(motor))
                tasks.append(task)
                cmds.append(f"#{one_actuator_info['电机id']}J=0")
            temp_acuatorr_infos = await asyncio.gather(*tasks, return_exceptions=True)
            cmd = " ".join(cmds)
            for actuator in temp_acuatorr_infos:
                print(f"当前促动器的信息: {actuator}")
                unique_key = actuator["拟合图名称"]  # 生成全局唯一的key，永远不会重复覆盖
                self.all_actuator_info[unique_key] = actuator
                self.logger.info(f"✅ 促动器{unique_key}结果已汇总到全局记录")

            if self.is_linearity_test:
                self.logger.info(f"✅ 本次测试的电机开始准备回到0脉冲, 以便下次线性度测试, cmd = {cmd}")
                await asyncio.wait_for(pmac_controller.exec_command(cmd), timeout=3)
                await asyncio.sleep(6)   # 等待电机回到0脉冲
            self.logger.info(f"✅ 放大器{amp_ip}所有通道测试完成")
        except Exception as e:
            self.logger.error(f"❌ 放大器测试出现异常: {str(e)}")
        finally:
            if sensor_reader:
                del sensor_reader
            pass

    async def main(self):
        async def _wrap_amp_test(amp_ip):       # 给每个放大器任务也加一层异常隔离，单个放大器异常不影响其他
            try:
                await self.one_amplifier_test(amp_ip)
            except Exception as e:
                self.logger.error(f"❌ 放大器{amp_ip}测试异常: {str(e)}")
        
        amplifier_info_list =  self.get_domestic_amplifier_info() if self.is_domestic else self.get_imported_amplifier_info()
        if not amplifier_info_list:
            self.logger.error("❌ 没有找到任何设备，程序退出")
            return
        try:
            tasks = []
            self.logger.info(f"✅ 找到放大器设备: {[amplifier for amplifier in amplifier_info_list]}")
            for amp_ip in amplifier_info_list:         # 测试设备上的所有放大器
                task = asyncio.create_task(_wrap_amp_test(amp_ip))
                tasks.append(task)
            await asyncio.gather(*tasks, return_exceptions=True)
            self.save_all_test_result_to_json()
        except Exception as e:
            self.logger.error(f"程序出现异常, err_code: {str(e)}")
        finally:
           # 优雅关闭
            for controller in Id2Controler.values():
                await controller.disconnect()
    async def __aenter__(self):
        """连接所有 PMAC 控制器"""
        self.logger.info("🔌 正在连接所有 PMAC 控制器...")
        for pmac_ip, pmac in Id2Controler.items():
            try:
                if not pmac.is_connected:
                    await pmac.connect()
                self.logger.info(f"✅ PMAC {pmac_ip} 已连接")
            except Exception as e:
                self.logger.error(f"❌ PMAC {pmac_ip}, 连接失败: {e}")
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """断开所有 PMAC 并清理资源"""
        self.logger.info("🔌 正在断开所有 PMAC 控制器...")
        for pmac_ip, pmac in Id2Controler.items():
            try:
                if pmac.is_connected:
                    await pmac.disconnect()
                self.logger.info(f"✅ PMAC {pmac_ip} 已断开")
            except Exception as e:
                self.logger.error(f"❌ PMAC {pmac_ip} 断开失败: {e}")
        
        # 关闭绘图进程池
        self.logger.info("📊 关闭绘图进程池...")
        GLOBAL_PLOT_POOL.shutdown(wait=True)
        return False
                
async def sensor_test(isDomestic:bool, isMergeCell:bool, mirrorId:int, isLinearityTest:bool):
    async with MirrorsTest(is_domestic=isDomestic, mirror_id=mirrorId, is_linearity_test=isLinearityTest) as test:
        await test.main()
        if test.is_linearity_test:
            excel_path=f"./mirror{test.mirror_id}_data/sensor_data.xlsx"
            print(f"{test.all_actuator_info}")
            excel_handler = ExcelDataHandler(excel_path, test.all_actuator_info, is_merge_cell=isMergeCell)
            excel_handler.main()

if __name__ == "__main__":
    try:
        asyncio.run(sensor_test(isDomestic=True, isMergeCell=True, mirrorId=4, isLinearityTest=True))
    except Exception as e:
        print(f"程序出现异常，正在退出..., {e}")

