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
import os
import re

log = logging.getLogger("agent.netinfo")

_AGENT_IMAGE = "hengshu/agent:latest"

_NIC_DUMP_CMD = (
    "python -c \""
    "import json,psutil;"
    "st=psutil.net_if_stats();ad=psutil.net_if_addrs();"
    "print(json.dumps([{'name':n,"
    "'ip':next((a.address for a in ad.get(n,[]) if a.family.name=='AF_INET'),''),"
    "'speed':getattr(st.get(n),'speed',0) or 0,"
    "'up':bool(getattr(st.get(n),'isup',False)),"
    "'virtual':n.startswith(('veth','br-','docker','lo','tailscale'))}"
    " for n in st if n!='lo']))\""
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
    except Exception as e:
        log.warning("collect_host_nics failed: %s", e)
        return []


# ---------------- 网卡配置（设 IP / up/down） ----------------

async def nic_config(name: str, ip_cidr: str = "", up: bool | None = None) -> dict:
    """host 网络 + NET_ADMIN 容器配置宿主机网卡。返回配置后的最新网卡清单。"""
    cmds: list[str] = []
    if ip_cidr:
        cmds += [f"ip addr flush dev {name}", f"ip addr add {ip_cidr} dev {name}"]
    if up is True:
        cmds.append(f"ip link set {name} up")
    elif up is False:
        cmds.append(f"ip link set {name} down")
    if not cmds:
        return {"ok": False, "error": "无可执行配置（ip_cidr/up 至少给一个）"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--network", "host", "--cap-add", "NET_ADMIN",
            _AGENT_IMAGE, "sh", "-c", " && ".join(cmds),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            return {"ok": False, "error": out.decode(errors="replace").strip()[:200]}
        return {"ok": True, "nics": await collect_host_nics()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- Ping 连通性测试（验证所选网卡间互通） ----------------

async def net_ping(target_ip: str, count: int = 3, timeout: float = 30.0) -> dict:
    """host 网络一次性容器 ping 目标 IP，回丢包率与平均 RTT。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--network", "host", _AGENT_IMAGE,
            "sh", "-c", f"ping -c {count} -W 2 {target_ip}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode(errors="replace")
        loss = re.search(r"(\d+)% packet loss", text)
        rtt = re.search(r"min/avg/max[^=]*=\s*[\d.]+/([\d.]+)/", text)
        if loss and int(loss.group(1)) < 100:
            return {"ok": True, "loss_pct": int(loss.group(1)),
                    "rtt_ms": float(rtt.group(1)) if rtt else None}
        return {"ok": False, "error": (text.strip().splitlines() or ["no output"])[-1][:200]}
    except TimeoutError:
        return {"ok": False, "error": f"timeout {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _run_quiet(args: list[str], timeout: float = 20.0) -> int:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return proc.returncode or 0
    except Exception:
        return -1


# ---------------- NCCL 环境部署（镜像就位 + 容器内自检） ----------------

async def nccl_env_deploy(image: str, progress_cb=None) -> dict:
    """确保 NCCL 测试镜像在本地（缺失则拉取），并容器内自检 torch.cuda+nccl。"""
    async def _log(msg: str) -> None:
        if progress_cb:
            await progress_cb("INFO", msg)

    rc, out = await _docker(["image", "inspect", image], timeout=20.0)
    if rc != 0:
        await _log(f"nccl-env ▸ 拉取镜像 {image}（大镜像需数分钟）…")
        rc, out = await _docker(["pull", image], timeout=1800.0)
        if rc != 0:
            return {"ok": False, "error": f"docker pull 失败: {out[-300:]}"}
        await _log("nccl-env ▸ 镜像拉取完成")
    rc, out = await _docker(
        ["run", "--rm", "--gpus", "all", "--entrypoint", "python3", image, "-c",
         "import torch;ok=torch.cuda.is_available();"
         "print('SELFTEST cuda=%s nccl=%s' % (ok, torch.cuda.nccl.version() if ok else '-'))"],
        timeout=180.0)
    if rc == 0 and "SELFTEST cuda=True" in out:
        await _log(f"nccl-env ▸ 自检通过: {out.strip().splitlines()[-1]}")
        return {"ok": True, "detail": out.strip().splitlines()[-1]}
    return {"ok": False, "error": f"自检失败: {out[-300:]}"}


async def _docker(args: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except TimeoutError:
        return -1, f"timeout {timeout}s"
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


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
                        cmd: str = "", env: dict | None = None,
                        timeout: float = 150.0) -> dict:
    """在本节点起一个 torchrun 容器跑 NCCL all_reduce，回传尾部输出。

    cmd 非空时用自定义指令（容器内 sh -c 执行，/data/hs_nccl_test.py 默认脚本已挂载）；
    env 为附加环境变量（NCCL_SOCKET_IFNAME/NCCL_IB_DISABLE 默认已带）。
    """
    # 脚本必须写进共享卷（/tmp 是 agent 容器内的，host daemon 挂载会解析成宿主机路径）
    path = "/data/hs_nccl_test.py"
    os.makedirs("/data", exist_ok=True)
    with open(path, "w") as f:
        f.write(_NCCL_SCRIPT)
    env_flags = ["-e", f"NCCL_SOCKET_IFNAME={nic}",
                 "-e", "NCCL_DEBUG=WARN",
                 "-e", f"RANK={rank}", "-e", f"WORLD_SIZE={world}",
                 "-e", f"MASTER_ADDR={master_addr}", "-e", f"MASTER_PORT={master_port}"]
    # RoCE 数据面：不预设 NCCL_IB_*，由调用方 env 指定（NCCL_IB_HCA / NCCL_IB_GID_INDEX 等）
    for k, v in (env or {}).items():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            env_flags += ["-e", f"{k}={v}"]
    if not cmd:
        cmd = (f"torchrun --nnodes={world} --nproc_per_node=1 --node_rank={rank} "
               f"--master_addr={master_addr} --master_port={master_port} /data/hs_nccl_test.py")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--network", "host", "--gpus", "all",
            *env_flags, "-v", "hengshu-agent-data:/data:ro",
            "--entrypoint", "sh", image, "-c", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode(errors="replace")
        ok = proc.returncode == 0 and "NCCL_RESULT ok" in text
        return {"ok": ok, "rank": rank, "tail": text.strip().splitlines()[-40:]}
    except TimeoutError:
        return {"ok": False, "rank": rank, "tail": [f"timeout {timeout}s"]}
    except Exception as e:
        return {"ok": False, "rank": rank, "tail": [f"{type(e).__name__}: {e}"]}


# ---------------- 模型库（统一 /models 约定） ----------------

async def list_models(host_dir: str) -> list[dict]:
    """列宿主机模型目录（一次性容器挂载只读 ls）。返回 [{name, size_mb, complete}]。

    complete：含 config.json / params.json（HF/Mistral）或 model_index.json（diffusers）
    或任一 .gguf/.safetensors 权重文件才算可用——半成品/空目录不参与"已有模型"判定。
    """
    cmd = ("python3 -c \""
           "import os,json;d='/m';"
           "def ok(p):"
           " fs=[f for _,_,xs in os.walk(p) for x in xs for f in [x]];"
           " return ('config.json' in fs) or ('params.json' in fs) or ('model_index.json' in fs) or any("
           "f.endswith('.gguf') or f.endswith('.safetensors') for f in fs);"
           "print(json.dumps([{'name':n,'size_mb':round(sum("
           "os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(os.path.join(d,n)) for f in fs"
           ")/2**20),'complete':ok(os.path.join(d,n))}"
           " for n in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d,n))]))\"")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-v", f"{host_dir}:/m:ro",
            _AGENT_IMAGE, "sh", "-c", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        for line in reversed(out.decode(errors="replace").strip().splitlines()):
            line = line.strip()
            if line.startswith("["):
                return json.loads(line)
        return []
    except Exception as e:
        log.warning("list_models failed: %s", e)
        return []


async def download_model(repo: str, host_dir: str, name: str,
                         progress_cb=None, timeout: float = 7200.0) -> dict:
    """在线拉取模型（modelscope，agent 镜像内临时 pip 安装）。"""
    async def _log(m: str) -> None:
        if progress_cb:
            await progress_cb("INFO", m)
    await _log(f"model ▸ 在线拉取 {repo} → {host_dir}/{name}（modelscope）")
    cmd = ("pip install -q -i https://mirrors.aliyun.com/pypi/simple/ modelscope && "
           f"modelscope download --model '{repo}' --local_dir '/m/{name}'")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-v", f"{host_dir}:/m",
            "--entrypoint", "sh", _AGENT_IMAGE, "-c", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        ok = proc.returncode == 0
        tail = out.decode(errors="replace").strip().splitlines()
        return {"ok": ok, "tail": tail[-6:]}
    except TimeoutError:
        return {"ok": False, "tail": [f"timeout {timeout}s"]}
    except Exception as e:
        return {"ok": False, "tail": [f"{type(e).__name__}: {e}"]}
