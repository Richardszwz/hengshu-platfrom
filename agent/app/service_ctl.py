"""服务启停（方案书 §3.8.1）：管理员自定义启动命令，双后端自动选择。

后端：
- docker：Agent 容器挂 /var/run/docker.sock，推理服务跑成容器
  （--gpus all、端口映射、env、自定义 image + start_cmd 为容器内命令）
- process：裸机降级，subprocess 进程组（开发机/mock 场景）

切换：AGENT_SERVICE_BACKEND=docker|process，默认按 /var/run/docker.sock 存在与否。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal

from fastapi import HTTPException
from pydantic import BaseModel, Field


class StartRequest(BaseModel):
    name: str
    image: str = "vllm/vllm-openai:latest"   # 推理容器镜像（管理员可改）
    start_cmd: str                            # 容器内启动命令（vllm serve ...）
    port: int
    workdir: str = "/workspace"
    env: dict[str, str] = Field(default_factory=dict)
    # 卷挂载：["宿主机路径:容器路径[:ro]"]，模型权重必须靠它进容器
    volumes: list[str] = Field(default_factory=list)


def _backend() -> str:
    forced = os.getenv("AGENT_SERVICE_BACKEND", "")
    if forced in ("docker", "process"):
        return forced
    return "docker" if os.path.exists("/var/run/docker.sock") else "process"


# 进程模式句柄（裸机降级用）
_procs: dict[str, asyncio.subprocess.Process] = {}


async def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise HTTPException(504, f"cmd timeout: {cmd[0]}")
    return p.returncode or 0, out.decode(errors="ignore")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


async def start_docker(req: StartRequest) -> str:
    if not _docker_available():
        raise HTTPException(500, "docker CLI unavailable")
    name = f"hs-svc-{req.name}"
    if await is_running(req.name):
        return f"container {name} already running"
    # 镜像不在本地则先拉取；气隙环境拉取失败也继续 run（镜像可能已离线装载）
    rc, _ = await _run(["docker", "image", "inspect", req.image], timeout=15.0)
    if rc != 0:
        await _run(["docker", "pull", req.image], timeout=600.0)
    env_flags: list[str] = []
    for k, v in req.env.items():
        env_flags += ["-e", f"{k}={v}"]
    vol_flags: list[str] = []
    for vol in req.volumes:
        # 仅接受 host:container[:opts] 形式，防注入
        parts = vol.split(":")
        if len(parts) in (2, 3) and parts[0].startswith("/") and parts[1].startswith("/"):
            vol_flags += ["-v", vol]
    rc, out = await _run([
        "docker", "run", "-d", "--name", name,
        "--restart", "unless-stopped",
        "--gpus", "all",
        "-p", f"{req.port}:{req.port}",
        "-w", req.workdir,
        *env_flags, *vol_flags,
        req.image,
        "sh", "-c", req.start_cmd,
    ], timeout=120.0)
    if rc != 0:
        raise HTTPException(500, f"docker run failed: {out[-400:]}")
    return f"container {name} started"


async def stop_docker(name: str) -> str:
    cname = f"hs-svc-{name}"
    rc, out = await _run(["docker", "rm", "-f", cname], timeout=60.0)
    if rc != 0 and "No such container" not in out:
        raise HTTPException(500, f"docker rm failed: {out[-300:]}")
    return f"container {cname} stopped"


async def logs_docker(name: str, tail: int) -> list[str]:
    cname = f"hs-svc-{name}"
    rc, out = await _run(["docker", "logs", "--tail", str(tail), cname], timeout=30.0)
    if rc != 0:
        return [f"[agent] {out.strip()[-200:]}" or f"[agent] container {cname} not found"]
    return out.splitlines()[-tail:]


async def start_process(req: StartRequest) -> str:
    p = await asyncio.create_subprocess_shell(
        req.start_cmd,
        cwd=req.workdir if os.path.isdir(req.workdir) else None,
        env={**os.environ, **req.env},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    _procs[req.name] = p
    return f"pid {p.pid} started"


async def stop_process(name: str) -> str:
    p = _procs.pop(name, None)
    if p is None:
        return "not running"
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        p.terminate()
    return "stopped"


async def logs_process(name: str, tail: int) -> list[str]:
    return [f"[agent] process-mode logs: {name} (stdout 未捕获, P1.2 落盘采集)"]


class ServiceCtl:
    async def start(self, req: StartRequest) -> str:
        if _backend() == "docker":
            return await start_docker(req)
        return await start_process(req)

    async def stop(self, name: str) -> str:
        if _backend() == "docker":
            return await stop_docker(name)
        return await stop_process(name)

    async def logs(self, name: str, tail: int = 100) -> list[str]:
        if _backend() == "docker":
            return await logs_docker(name, tail)
        return await logs_process(name, tail)


async def is_running(name: str) -> bool:
    """实例真实运行状态（docker inspect / 进程表）。"""
    if _backend() == "docker":
        rc, out = await _run(["docker", "inspect", "-f", "{{.State.Running}}",
                              f"hs-svc-{name}"], timeout=15.0)
        return rc == 0 and out.strip() == "true"
    p = _procs.get(name)
    return p is not None and p.returncode is None
