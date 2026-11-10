from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
import atexit
from datetime import datetime
import subprocess
from typing import Optional, Dict
import asyncio
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

from mirror.ocs_utils import get_ocs_logger


class Amplifier:  # 国产放大器
    def __init__(self, host:str, port:int = 502, timeout:float = 3.0, retries:int= 3, slave_id:int = 1):
        self.client: Optional[AsyncModbusTcpClient] = None
        self.stop_event = asyncio.Event()  # 用于控制循环停止的事件对象
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.slave_id = slave_id
        self.logger = get_ocs_logger(f"Amplifier_{self.host}")  # 每个设备一个独立的logger实例

    async def connect(self) -> None:
        """异步建立Modbus TCP连接"""
        if self.client and self.client.connected:
            self.client.close()

        self.client = AsyncModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
            retries=self.retries,
            # framer='socket',  # 使用socket framer，适配大多数设备
            keepalive=True,     # 直接开启TCP保活，减少连接中断风险
        )
        self.client.comm_params.handle_local_echo = False

        connected = await self.client.connect()
        
        if not connected:
            self.logger.error(f"无法连接设备 {self.host}:{self.port}，请检查网络/配置")
            self.client.close()
            raise ConnectionError(f"无法连接设备 {self.host}:{self.port}，请检查网络/配置")
        self.logger.info(f"已成功连接设备 {self.host}:{self.port}")

    async def disconnect(self) -> None:
        """异步断开连接释放资源"""
        if self.client and self.client.connected:
            self.client.close()
            self.logger.info(f"设备_{self.host} 连接已关闭")

    async def get_val_by_channel(self, index: int, reg_pair: list, results: Dict[str, float]) -> None:
        decoder = BinaryPayloadDecoder.fromRegisters(
            reg_pair,
            byteorder=Endian.BIG,
            wordorder=Endian.BIG,
        )
        val = decoder.decode_32bit_float()
        channel = int(index / 2 + 1)
        self.logger.info(f"通道{channel}测量值: {val:.4f}")
        results[f"channel_{channel}"] = val

    async def read_channels_measure(self):
        """
            功  能：异步读取所有通道测量值
            参  数：无参
            返回值: 成功返回测量值字典, 失败返回None
        """
        if not self.client or not self.client.connected:
            self.logger.error("尚未连接设备, 请先调用connect()")
            return None

        start_addr = 1  # 通道1的寄存器起始地址：1+（n-1）*2
        reg_count = 16  # 32位浮点数占2个连续寄存器, 8个通道共16个寄存器

        try: # 使用03H功能码读取保持寄存器
            response = await self.client.read_holding_registers(
                address=start_addr,
                count=reg_count,
                slave=self.slave_id  # 通讯地址默认为01
            )
            if response.isError():
                self.logger.error(f"读取失败: {response}")
                return None

            # self.logger.info(f"response = {response}, response.registers: {response.registers}, len = {len(response.registers)}")
            try:
                results = {}
                for index in range(0, len(response.registers), 2):
                    reg_pair = response.registers[index:index+2]
                    decoder = BinaryPayloadDecoder.fromRegisters(reg_pair, byteorder=Endian.BIG, wordorder=Endian.BIG)
                    val = decoder.decode_32bit_float()
                    channel = int(index / 2 + 1)
                    self.logger.info(f"{self.host}--通道{channel}测量值: {val:.4f}")
                    results[f"channel_{channel}"] = val
                return results
            except Exception as e:
                self.logger.error(f"数据解析异常: {str(e)}", exc_info=True)
                return None
        except ModbusException as e:
                self.logger.error(f"Modbus协议错误: {str(e)}")
                return None
        except Exception as e:
                self.logger.error(f"未知错误: {str(e)}", exc_info=True)
                return None     
    
    # 上层调用接口，获取设备8个通道的测量值
    async def read_data(self):
        val = await self.read_channels_measure()
        values = []
        if val is not None:
            # self.logger.info(f"[{self.host}] 当前测量值: {val}")
            values.append(val["channel_1"])
            values.append(val["channel_2"])
            values.append(val["channel_3"])
            values.append(val["channel_4"])
            values.append(val["channel_5"])
            values.append(val["channel_6"])
            values.append(val["channel_7"])
            values.append(val["channel_8"])
            timestamp = datetime.now()
            result = (self.host, timestamp, values)
            # self.logger.info(f"host:{self.host}, fetch data: {result}")
            return result
        else:
            self.logger.error("获取测量值失败, 返回None")
            return (self.host,  datetime.now(), values)


    # 本地调用接口，循环测试单个放大器的8个通道
    async def display_all_channels(self) -> None:
        """读取并显示所有通道测量值"""
        while not self.stop_event.is_set():
            await self.read_channels_measure()
            await asyncio.sleep(0.1)  # 每0.1秒读取一次，避免过于频繁
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        self.stop_event.set()  # 停止循环
        return False           # 返回 False：不抑制异常（如果业务逻辑出错，会正常抛出）

    def __del__(self):
        # 先检查当前事件循环是否还存活
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有正在运行的事件循环，说明程序已经退出、loop已关闭，直接跳过close操作
            return
        
        if loop.is_closed():
            return
    
        if self.client and self.client.connected:
            self.client.close()
            self.logger.info(f"设备_{self.host} 连接已关闭")
        self.stop_event.set()  # 停止循环


if __name__ == "__main__":
    """
    运行前检查：
    1. PC和设备处于同一网段，可ping通设备IP
    2. 防火墙放行502端口，设备上电网线连接正常
    安装依赖：pip install pymodbus
    """
    
    @atexit.register  # Ctrl+c 时触发
    def auto_kill_modbus_connection():
            """程序退出时自动关闭连接到192.168.0.102:502的所有进程"""
            try:
                # 执行查找并终止进程的命令
                # cmd = "lsof -i TCP | grep '192.168.0.13:502' | awk '{print $2}' | xargs kill -9"
                cmd = "lsof -ti tcp:502 | xargs -r kill -9"

                subprocess.run(cmd, shell=True, check=True)
                print("✅ 已自动关闭所有目标连接进程")
            except subprocess.CalledProcessError:
                # 如果没有匹配进程，忽略错误即可
                print("⚠️ 未找到目标进程，无需关闭")
    
    params = {
        "host":     "192.168.0.104" ,   # 设备IP
        "port":     502,                # Modbus TCP端口，默认502
        "timeout":  3,                  # 连接/响应超时(秒)
        "retries":  3,                  # 重试次数
        "slave_id": 1                   # Modbus从站地址，文档默认1
    }

    async def main():
        async with Amplifier(**params) as reader:
            await reader.display_all_channels()

    asyncio.run(main())
