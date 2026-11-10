
"""工具模块"""
import logging, logging.handlers
import multiprocessing, atexit, threading
import time


_log_queue = None
_log_listener = None
_listener_started = False
_init_lock = threading.Lock()


def _start_listener_if_needed(file_handler):
    """仅在主进程且尚未启动时，创建并启动 QueueListener"""
    global _log_queue, _log_listener, _listener_started
    if multiprocessing.current_process().name != "MainProcess":
        return
    with _init_lock:
        if _listener_started:
            return
        try:
            _log_queue = multiprocessing.Queue(-1)
            _log_listener = logging.handlers.QueueListener(_log_queue, file_handler, respect_handler_level=True)
            _log_listener.start()
            _listener_started = True
            atexit.register(stop_log_listener)
        except Exception as e:
            import sys
            print(f"[OCS_Logger] QueueListener 启动失败，降级写文件: {e}", file=sys.stderr)
            _log_queue = None

def stop_log_listener():
    global _log_queue, _log_listener, _listener_started
    if _log_listener is not None:
        try:
            _log_listener.stop()
        finally:
            _log_listener = None
            _log_queue = None
            _listener_started = False


class OCS_Logger(logging.Logger):
    """日志记录器类"""
    FORMATTER = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    def __init__(self, name:str, debug:bool=False):
        level = logging.DEBUG if debug else logging.INFO
        super().__init__(name=name, level=level)

        if self.handlers:
            return  # 避免重复添加处理器

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(self.FORMATTER)
        self.addHandler(stream_handler)
        
        if multiprocessing.current_process().name == "MainProcess":
            self._setup_main_process_file_handler(name, level)
        else:
            self._setup_worker_file_handler(level)


    def _setup_main_process_file_handler(self, name:str, level):
        """主进程文件日志处理: 优先尝试队列监听，失败则降级为直接写文件"""
        global _log_listener
        
        # listener 已启动：直接复用队列，不 new 文件句柄
        if _log_listener is not None:
            qh = logging.handlers.QueueHandler(_log_queue)
            qh.setLevel(level)
            self.addHandler(qh)
            return
        
        file_handler = logging.FileHandler(name+".log", encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(self.FORMATTER)
        _start_listener_if_needed(file_handler)
        if _log_queue is not None:
            # self.addHandler(logging.handlers.QueueHandler(_log_queue))
            qh = logging.handlers.QueueHandler(_log_queue)
            qh.setLevel(level)
            self.addHandler(qh)
        else:
            self.addHandler(file_handler)

    def _setup_worker_file_handler(self, level):
        global _log_queue

        start = time.time()
        while _log_queue is None:
            if time.time() - start > 10:
                raise RuntimeError("日志队列未初始化：请在 fork 子进程前先创建一次 OCS_Logger")
            time.sleep(0.1)
        handler = logging.handlers.QueueHandler(_log_queue)
        handler.setLevel(level)
        self.addHandler(handler)


_ocs_loggers = {}                              
def get_ocs_logger(name, debug=False):         
    if name not in _ocs_loggers:
        _ocs_loggers[name] = OCS_Logger(name, debug)
    return _ocs_loggers[name]


def silence_loggers(prefixes, level=logging.WARNING):
    """阻止某些第三方库日志刷屏

    Usage:
        silence_loggers(['gsv86', 'abc',])
    """
    for name, logger in logging.Logger.manager.loggerDict.items():
        if not isinstance(logger, logging.Logger):
            continue
        if any(name.startswith(p) for p in prefixes):
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(level)
        


######################################################################################################

import asyncio, time
class PeriodicTimer:
    """固定速率-计时器, 核心受限于sleep函数, 精度只能保毫秒级"""

    def __init__(self, interval, start_time=None):
        self.interval = interval
        self.next_time = (start_time if start_time is not None else time.monotonic()) + interval
    
    def waiting(self):
        now = time.monotonic()
        diff = max(0, self.next_time - now)
        time.sleep(diff)
        self.next_time += self.interval

    async def waiting_async(self):
        now = time.monotonic()
        diff = max(0, self.next_time - now)
        await asyncio.sleep(diff)
        self.next_time += self.interval


