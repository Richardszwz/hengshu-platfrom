"""宿主机网卡发现 + NCCL 多机联通/带宽测试。

Agent 容器跑在桥接网络里看不到宿主机网卡 → 用自身镜像起一次性
`--network host` 容器拿宿主接口（psutil）。

NCCL 测试：各节点用本地 vllm 镜像（自带 torch+NCCL，免拉新镜像）
torchrun 跑 all_reduce；Agent 负责起容器、限时、回传尾部输出。
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("agent.netinfo")

_AGENT_IMAGE = "hengshu/agent:latest"

_NIC_DUMP_CMD = (
    "python -c \""
    "import json,psutil;"
    "st=psutil.net_if_stats();ad=psutil.net_if_addrs();"
    "print(json.dumps([{'name':n,'ip':a.address,"
    "'speed':getattr(st.get(n),'speed',0) or 0,"
    "'up':bool(getattr(st.get(n),'isup',False))}"
    " for n,addrs in ad.items() for a in addrs if a.family.name=='AF_INET'"
    " and not a.address.startswith('127.')]))\""
)


def parse_nics(text: str) -> list[dict]:
    """解析一次性容器打印的 JSON 行（纯函数，可单测）。"""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("["):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return []


async def collect_host_nics(timeout: float = 25.0) -> list[dict]:
    """一次性 host 网络容器采集宿主机网卡（失败返回 []，不影响心跳）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--network", "host", _AGENT_IMAGE,
            "sh", "-c", _NIC_DUMP_CMD,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return parse_nics(out.decode(errors="replace"))
    except Exception as e:  # noqa: BLE001
        log.warning("collect_host_nics failed: %s", e)
        return []


# ---------------- NCCL 测试 ----------------

_NCCL_SCRIPT = r'''
import os, time, torch, torch.distributed as dist
dist.init_process_group("nccl")
rank = dist.get_rank()
dev = torch.device(f"cuda:{rank % max(torch.cuda.device_count(),1)}")
torch.cuda.set_device(dev)
x = torch.randn(16 * 1024 * 1024, device=dev)  # 64MB
for _ in range(3):
    dist.all_reduce(x)                          # 热身
torch.cuda.synchronize(); t0 = time.time()
for _ in range(10):
    dist.all_reduce(x)
torch.cuda.synchronize(); dt = time.time() - t0
if rank == 0:
    w = dist.get_world_size()
    bus = x.numel() * 4 * 2 * (w - 1) / w * 10 / dt / 1e9
    print(f"NCCL_RESULT ok bus_bandwidth_GBps={bus:.2f} world={w} tensor_mb=64")
dist.destroy_process_group()
'''


async def run_nccl_test(*, rank: int, world: int, master_addr: str,
                        master_port: int, nic: str, image: str,
                        timeout: float = 150.0) -> dict:
    """在本节点起一个 torchrun 容器跑 NCCL all_reduce，回传尾部输出。"""
    path = "/tmp/hs_nccl_test.py"
    with open(path, "w") as f:
        f.write(_NCCL_SCRIPT)
    env_flags = ["-e", f"NCCL_SOCKET_IFNAME={nic}",
                 "-e", "NCCL_DEBUG=WARN",
                 "-e", "NCCL_IB_DISABLE=1"]
    cmd = (f"torchrun --nnodes={world} --nproc_per_node=1 --node_rank={rank} "
           f"--master_addr={master_addr} --master_port={master_port} /tmp/hs_nccl_test.py")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--network", "host", "--gpus", "all",
            *env_flags, "-v", f"{path}:/tmp/hs_nccl_test.py:ro",
            "--entrypoint", "sh", image, "-c", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode(errors="replace")
        ok = proc.returncode == 0 and "NCCL_RESULT ok" in text
        return {"ok": ok, "rank": rank, "tail": text.strip().splitlines()[-12:]}
    except asyncio.TimeoutError:
        return {"ok": False, "rank": rank, "tail": [f"timeout {timeout}s"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "rank": rank, "tail": [f"{type(e).__name__}: {e}"]}
