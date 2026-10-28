"""PMAC(CK3M/CK5M/CK3E)控制接口模块"""                                                                                                                                                                                                                                                                                                                                                                                                   

import asyncio, asyncssh, logging
from dataclasses import dataclass, asdict
from typing import Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("asyncssh").setLevel(logging.WARNING)


@dataclass() # frozen=True：不可修改
class SSH_Config:
    host:str        = "192.168.0.200"
    port:int        = 22
    username:str    = "root"
    password:str    = "deltatau"
    connect_timeout:float  = 10


class PMAC_Controller:
    SSH_CONN_TIMEOUT = 3.0 
    GPA_RECV_TIMEOUT = 0.01
    SSH_LOGIN_PROMPT = ":/opt/ppmac# "
    GPA_SEP_PROMPT   = "\r\n"
    GPA_LOGIN_ACK    = "\x06\r\n"
    GPA_ACK_PROMPT   = "\x06\r\n\x06\r\n"
    GPA_LOGIN_PROMPT = "STDIN Open for ASCII Input\r\n"
    VARIABLES_MAP    = { 
            "RealPos":  "P8192",
            "RealVel":  "P8193",
            "AmpEna":   "Motor[{axis}].AmpEna",
            "ActPos":   "Motor[{axis}].ActPos",
            "DesPos":   "Motor[{axis}].DesPos",
            "HomePos":  "Motor[{axis}].HomePos",
    }  


    def __init__(self, ssh_config: Optional[SSH_Config]):
        self.conn =  self.writer = self.stdout = self.stderr = None
        self._cmdLock = asyncio.Lock()                      # 命令串行化
        self.ssh_config = ssh_config
        self.logger = logging.getLogger("PMAC")
        self.logger.setLevel(logging.DEBUG)
        self.logger.info(f"PMAC_Controller is starting up ...")


    @property
    def is_connected(self) -> bool:
        return (
                all([self.conn, self.writer, self.stdout, self.stderr])
                and not self.conn.is_closed()
                and not self.writer.is_closing()
        )


    async def disconnect(self) -> None:
        if (self.writer is not None) and (not self.writer.is_closing()):
            self.writer.close()
            await self.writer.wait_closed()
        if (self.conn is not None) and (not self.conn.is_closed()):
            self.conn.close()
            await self.conn.wait_closed()
        self.conn =  self.writer = self.stdout = self.stderr = None


    async def connect(self) -> bool:
        await self.disconnect()
        try:
            self.logger.info(f"Connecting to PMAC ...")
            # 1. 建立SSH连接
            self.conn = await asyncssh.connect(
                    **asdict(self.ssh_config),
                    options=asyncssh.SSHClientConnectionOptions(known_hosts=None),  # known_hosts=None 表示不验证服务器的主机密钥
            )
            try:
                # 2. 启动GP-ASCII会话
                self.writer, self.stdout, self.stderr = await self.conn.open_session(term_type="vt100")  # 用于在建立 SSH 连接后打开一个新的会话（session）。通过这个会话，你可以执行远程命令、启动子进程等
                
                # 3. 验证SSH登录
                output = await asyncio.wait_for(self.stdout.readuntil(self.SSH_LOGIN_PROMPT), timeout=self.SSH_CONN_TIMEOUT)
                
                # 4. 启动gpascii模式
                await self._write("gpascii -2 -f")
                output = await asyncio.wait_for(self.stdout.readuntil(self.GPA_LOGIN_ACK), timeout=self.SSH_CONN_TIMEOUT)  # stdout.readuntil:允许从标准输出流中读取数据，直到遇到指定的分隔符为止
                
                # 5. 验证GP-ASCII会话
                if self.GPA_LOGIN_PROMPT in output:
                    self.logger.info("Connection to PMAC is OK")
                    return True
                self.logger.warning(f"GPA is FAILED: MISMATCH LOGIN PROMPT")
                return False
            except asyncio.TimeoutError:
                self.logger.warning(f"GPA is FAILED: TIMEOUT (>{self.SSH_CONN_TIMEOUT})")
                return False
            except Exception as err:
                self.logger.warning(f"GPA is FAILED: {err!r}")
                return False
        except Exception as err:
            self.logger.warning(f"SSH connected FAILED: {err!r}")
            return False


    async def _write(self, cmd:str) -> None:
        """发命令--底层函数"""
        self.writer.write(cmd + PMAC_Controller.GPA_SEP_PROMPT)
        await self.writer.drain()


    async def _read(self) -> list[str]:
        """收结果--底层函数"""
        res = await self.stdout.readuntil(PMAC_Controller.GPA_ACK_PROMPT)
        return [item for item in res.replace(PMAC_Controller.GPA_ACK_PROMPT, "").split(self.GPA_SEP_PROMPT) if item]

    async def exec_command(self, cmd:str) -> tuple[bool, list[float]]:
        """发明令收结果--还保证串行化，防止同一链路上多命令返回乱套了"""
        if not self.is_connected:
            await self.connect()
        async with self._cmdLock:
            try:
                await self._write(cmd)
                res = await self._read()
                return (True, res) if cmd in res else (False, res)
            except Exception as err:
                self.logger.warning(f"EXEC {cmd} FAILED: {err!r}")
            return (False, [])

    def get_variable_name(self, var_type: str, axis: int = 1) -> str:
        """根据变量类型和轴号生成实际的PMAC命令"""
        template = self.VARIABLE_TEMPLATES.get(var_type)
        if template is None:
            raise ValueError(f"Unknown variable type: {var_type}")
        return template.format(axis=axis)

    async def get_variable(self, var_type:str, axis: int = 1) -> float:
        """获取单个值"""
        cmd = self.get_variable_name(var_type, axis)
        ok, res = await self.exec_command(cmd)
        return float(res[-1]) if ok else None


    async def get_variables(self, var_types: list[str], axis: int = 1) -> list[float]:
        """获取多个值"""
        cmd = ";".join(self.get_variable_name(vt, axis) for vt in var_types)
        ok, res = await self.exec_command(cmd)
        return [float(val) for val in res[1:]] if ok else []


    async def __aenter__(self):
        if not await self.connect():
            raise ConnectionError(f"Failed to connect to PMAC (check logs for details)")
        return self


    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        return False    # 返回 False：不抑制异常（如果业务逻辑出错，会正常抛出）




