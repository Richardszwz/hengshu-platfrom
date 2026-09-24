"""服务启停（方案书 §3.8.1）：自定义 start_cmd 经 Agent 执行，setsid 脱离生命周期。

P0 提供本地进程实现（subprocess + 进程组）；
P2 接入 Docker / systemd 后端时实现同一接口。
"""
from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass, field

from pydantic import BaseModel


class StartRequest(BaseModel):
    name: str                    # 服务/实例名
    start_cmd: str               # 用户自定义启动命令（如 vllm serve ...）
    port: int
    env: dict[str, str] = {}


@dataclass
class RunningService:
    name: str
    port: int
    proc: asyncio.subprocess.Process
    log_tail: list[str] = field(default_factory=list)


class ServiceCtl:
    def __init__(self) -> None:
        self.running: dict[str, RunningService] = {}

    async def start(self, req: StartRequest) -> str:
        if req.name in self.running and self.running[req.name].proc.returncode is None:
            raise RuntimeError(f"{req.name} already running")
        proc = await asyncio.create_subprocess_shell(
            req.start_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,          # setsid 等价：杀 Agent 不带走服务
            env={**_base_env(), **req.env},
        )
        svc = RunningService(name=req.name, port=req.port, proc=proc)
        self.running[req.name] = svc
        asyncio.create_task(self._pump_logs(svc))
        return f"started pid={proc.pid}"

    async def stop(self, name: str) -> str:
        svc = self.running.get(name)
        if not svc:
            return "not running"
        try:
            # 先优雅TERM，3s后KILL（进程组）
            svc.proc.terminate()
            await asyncio.wait_for(svc.proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            svc.proc.kill()
        return "stopped"

    async def logs(self, name: str, tail: int = 100) -> list[str]:
        svc = self.running.get(name)
        return svc.log_tail[-tail:] if svc else []

    async def _pump_logs(self, svc: RunningService) -> None:
        assert svc.proc.stdout
        while True:
            line = await svc.proc.stdout.readline()
            if not line:
                break
            svc.log_tail.append(line.decode(errors="replace").rstrip())
            if len(svc.log_tail) > 500:
                del svc.log_tail[:100]


def _base_env() -> dict[str, str]:
    import os

    return dict(os.environ)
