import struct
from multiprocessing import shared_memory, Event
from mirror.mirror_control.config import (AMP_DATA_BYTES, DATA_BUFF_BYTES, SENSORS_PER_AMP)
from mirror.ocs_utils import get_ocs_logger, PeriodicTimer
from datetime import datetime
from mirror.amplifier.domestic_amplifier import Amplifier as DomesticAmplifier
from mirror.amplifier.imported_amplifier import Amplifier as ImportedAmplifier


# 核心构造映射表
_AMP_FACTORY = {
    True: lambda info, _id, rate: DomesticAmplifier(info),
    False: lambda info, _id, rate: ImportedAmplifier(path=info, amp_id=_id, data_rate=rate)
}


async def collector(amp_info:str, amp_id:int, shm_name:str, stop_event:Event, start_time:float, *, interval:float=2.0, data_rate:float=1.0, debug=False, is_domestic:bool = True):   # 单独的* 强制后续参数必须用关键字传递
    """单个Amplifier的子进程执行函数"""
    logger = get_ocs_logger(name=f"{amp_info}", debug=debug)
    amplifier = _AMP_FACTORY[is_domestic](amp_info, amp_id, data_rate)
    try:
        await amplifier.connect()
    except Exception as e:
        logger.error(f"❌ 无法连接放大器 {amp_info}: {str(e)}")
        return

    logger.info(f"{amp_info}: amplifier is CONNECTED")
    try:
        shm = shared_memory.SharedMemory(name=shm_name)
        buffer = memoryview(shm.buf)                            # 创建共享内存的内存视图对象，方便后续读写共享内存（零拷贝）
        mem_data_start = amp_id * AMP_DATA_BYTES                # 共享内存区中, 本放大器对应的数据存储起始位置
        mem_timestamp_start = mem_data_start + DATA_BUFF_BYTES  # 时间戳目前使用time.time(), float型。好对齐，省事，没使用int型。
        logger.info(f"{amp_info}: shared memory is CONNECTED")

        timer = PeriodicTimer(interval, start_time)
        while not stop_event.is_set():
            timer.waiting()

            # 读放大器数据，写入共享内存
            try:
                data = await amplifier.read_data()
            except RuntimeError as err:
                logger.warning(err)
                continue
            
            ts = data[1].timestamp()
            values = data[2]
            if not values or len(values)!=SENSORS_PER_AMP:                    # 数据检查
                values = [float('nan')]*SENSORS_PER_AMP
            struct.pack_into(f"{SENSORS_PER_AMP}d", buffer, mem_data_start, *values)        # 8个double型，字节顺序操作系统默认
            struct.pack_into(f"{SENSORS_PER_AMP}d", buffer, mem_timestamp_start, *(ts,)*SENSORS_PER_AMP)
            logger.debug(f"{amp_info}: [{datetime.now().strftime('%H:%M:%S.%f')[:-5]}]: {values}")
    except KeyboardInterrupt:
        pass
    finally:
        del buffer
        shm.close()
        logger.info(f"{amp_info}: shared memory is CLOSED")
        logger.info(f"{amp_info}: amplifier is CLOSED")



