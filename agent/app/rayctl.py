"""Ray 集群管控（多机 TP 并行的底座）：head/worker 容器启停 + 集群规模探测。

容器一律 --network host（NCCL 走 RoCE 网卡，桥接看不见）+ --shm-size 大共享内存
（Ray object store 用 /dev/shm）。镜像用节点本地自带 Ray 的 vllm-openai。
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger("agent.rayctl")


async def _run(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def _cname(name: str, role: str) -> str:
    return f"hs-ray-{role}-{name}"


async def ray_start(*, name: str, role: str, image: str, nic: str,
                    nic_ip: str, env: dict | None = None,
                    master_addr: str = "", port: int = 6379,
                    shm_size: str = "16g") -> dict:
    """role=head → ray start --head；role=worker → ray start --address=master。"""
    cname = _cname(name, role)
    await _run(["rm", "-f", cname], timeout=15.0)
    if role == "head":
        ray_cmd = (f"ray start --head --port={port} --dashboard-host=0.0.0.0 "
                   f"--node-ip-address={nic_ip} --block")
    else:
        ray_cmd = (f"ray start --address={master_addr}:{port} "
                   f"--node-ip-address={nic_ip} --block")
    env_flags = ["-e", f"NCCL_SOCKET_IFNAME={nic}", "-e", f"VLLM_HOST_IP={nic_ip}",
                 "-e", "NCCL_DEBUG=WARN"]
    for k, v in (env or {}).items():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            env_flags += ["-e", f"{k}={v}"]
    rc, out = await _run(
        ["run", "-d", "--name", cname, "--restart", "unless-stopped",
         "--network", "host", "--gpus", "all", "--shm-size", shm_size,
         *env_flags, "--entrypoint", "sh", image, "-c", ray_cmd],
        timeout=90.0)
    if rc != 0:
        return {"ok": False, "error": out.strip()[-300:]}
    return {"ok": True, "container": cname}


async def ray_nodes_online(name: str) -> int:
    """head 容器里 ray status，解析在线节点数（0 = head 还没起/无 ray）。"""
    rc, out = await _run(["exec", _cname(name, "head"),
                          "ray", "status"], timeout=30.0)
    if rc != 0:
        return 0
    m = re.findall(r"node_", out)          # ray status 每个节点一行 node_<ip>
    if not m:                               # 新版输出 "N node(s)" 样式兜底
        m2 = re.search(r"(\d+)\s+node", out)
        return int(m2.group(1)) if m2 else 0
    return len(set(m))


async def ray_stop(name: str) -> dict:
    """停掉本节点上该模型的 ray 容器（head 或 worker 角色自适应）。"""
    stopped = []
    for role in ("head", "worker"):
        rc, _ = await _run(["rm", "-f", _cname(name, role)], timeout=20.0)
        if rc == 0:
            stopped.append(role)
    return {"ok": True, "stopped": stopped}
