"""语义引擎（embedding）一键部署：本机或集群节点，Ollama 框架。

设计：客户选目标（本机=console 所在机 / 在线节点）→ 自动检测/安装 Ollama →
自动 pull 模型 → 自动起服务 → 写 hs:hse:config.embedding_url 接入 HSE。
部署是长任务（拉模型数 GB），后台执行，进度写事件流；凭证只用于本次部署不落库。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import asyncssh
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..license_guard import require_active_base
from ..security import require_role

router = APIRouter(prefix="/api/hse/embed", tags=["hse-embed"])
CONFIG_KEY = "hs:hse:config"

# 部署状态（单任务，幂等重入由前端控制）
_state: dict = {"running": False, "target": "", "step": "", "error": ""}


class SshIn(BaseModel):
    username: str = Field(min_length=1)
    password: str | None = None
    private_key: str | None = None


class DeployIn(BaseModel):
    target: str = "local"          # "local" 或节点 ID
    port: int = 11434
    model: str = "bge-m3"
    models_dir: str = ""           # 模型存放位置（空=Ollama 默认）
    ssh: SshIn | None = None       # target 为节点时必填（凭证不落库）


async def _event(r, level: str, msg: str) -> None:
    try:
        pipe = r.pipeline()
        pipe.rpush("hs:events", json.dumps(
            {"ts": time.time(), "level": level, "msg": msg}, ensure_ascii=False))
        pipe.ltrim("hs:events", -500, -1)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        pass


async def _probe(url: str, model: str, timeout: float = 6.0) -> tuple[bool, str]:
    """健康检查：/api/tags 列出模型 + 试一次真实 embedding。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            tags = (await c.get(f"{url}/api/tags")).json()
            names = [m.get("name", "") for m in tags.get("models", [])]
            if not any(n == model or n.startswith(f"{model}:") for n in names):
                return False, f"模型列表无 {model}（现有: {', '.join(names) or '空'}）"
            d = (await c.post(f"{url}/v1/embeddings",
                              json={"model": model, "input": "ping"})).json()
            if d.get("data", [{}])[0].get("embedding"):
                return True, "ok"
            return False, "embedding 返回异常"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ---------- Linux 节点（SSH） ----------
_LINUX_SCRIPT = r"""
set -e
PORT={port}; MODEL="{model}"; MDIR="{models_dir}"
echo "== 检查 ollama =="
if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama 未安装，自动安装…"
  curl -fsSL https://ollama.com/install.sh | sudo -n sh || curl -fsSL https://ollama.com/install.sh | sh
fi
ollama --version
echo "== 配置启动（port=$PORT） =="
ENVV="OLLAMA_HOST=0.0.0.0:$PORT"
[ -n "$MDIR" ] && ENVV="$ENVV OLLAMA_MODELS=$MDIR"
if [ "$PORT" = "11434" ] && systemctl is-active ollama >/dev/null 2>&1; then
  echo "复用 systemd ollama 服务"
else
  pkill -f "ollama serve" 2>/dev/null || true
  sleep 1
  env $ENVV setsid nohup ollama serve > /tmp/ollama-hse.log 2>&1 < /dev/null &
  echo "已启动 ollama serve（$ENVV）"
fi
echo "== 等待端口就绪 =="
for i in $(seq 1 45); do
  curl -s -m 2 "http://127.0.0.1:$PORT/api/tags" >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 2 "http://127.0.0.1:$PORT/api/tags" >/dev/null || {{ echo "端口未就绪"; exit 1; }}
echo "== 拉取模型 $MODEL =="
OLLAMA_HOST="127.0.0.1:$PORT" ollama pull "$MODEL"
echo "== 完成 =="
"""


async def _deploy_node(r, node_id: str, req: DeployIn) -> str:
    conn_info = await r.hgetall(f"hs:conn:{node_id}")
    if not conn_info:
        raise RuntimeError(f"节点 {node_id} 无连接信息（先纳管）")
    if not req.ssh or (not req.ssh.password and not req.ssh.private_key):
        raise RuntimeError("部署到节点需提供本次 SSH 凭证（不落库）")
    host = conn_info["host"]
    kw: dict = {"host": host, "port": int(conn_info.get("port", 22)),
                "username": req.ssh.username, "known_hosts": None, "connect_timeout": 15}
    if req.ssh.private_key:
        kw["client_keys"] = [asyncssh.import_private_key(req.ssh.private_key)]
    else:
        kw["password"] = req.ssh.password
    script = _LINUX_SCRIPT.format(port=req.port, model=req.model,
                                  models_dir=req.models_dir)
    async with asyncssh.connect(**kw) as conn:
        await _event(r, "INFO", f"embed ▸ {node_id}({host}) 开始部署 ollama+{req.model}")
        proc = await conn.create_process("bash -s", input=script)
        async for line in proc.stdout:
            line = line.rstrip()
            if line:
                await _event(r, "INFO", f"embed ▸ {node_id}: {line[:120]}")
        await proc.wait()
        if proc.exit_status != 0:
            err = ""
            async for line in proc.stderr:
                err += line
            raise RuntimeError(f"远端部署失败(rc={proc.exit_status}): {err[-200:]}")
    return f"http://{host}:{req.port}"


# ---------- 本机 ----------
async def _local_run(cmd: str, timeout: float = 600.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"超时({timeout}s)"
    return proc.returncode or 0, out.decode(errors="replace")


async def _deploy_local(r, req: DeployIn) -> str:
    win = sys.platform.startswith("win")
    if win and req.port != 11434:
        raise RuntimeError("本机(Windows)部署仅支持默认端口 11434"
                           "（自定义端口请部署到集群节点）")
    rc, out = await _local_run("ollama --version" if not win else "where ollama")
    if rc != 0:
        await _event(r, "INFO", "embed ▸ 本机无 ollama，自动安装…")
        if win:
            rc, out = await _local_run(
                "winget install -e --id Ollama.Ollama --silent --accept-package-agreements "
                "--accept-source-agreements", timeout=600)
            if rc != 0:
                raise RuntimeError(f"winget 安装 ollama 失败：{out[-200:]}")
            await asyncio.sleep(8)  # 等托盘自启
        else:
            rc, out = await _local_run("curl -fsSL https://ollama.com/install.sh | sh", 600)
            if rc != 0:
                raise RuntimeError(f"安装 ollama 失败：{out[-200:]}")
            await _local_run("setsid nohup ollama serve > /tmp/ollama-hse.log 2>&1 &", 5)
            await asyncio.sleep(3)
    else:
        await _event(r, "OK", f"embed ▸ 本机 ollama 已就绪: {out.strip().splitlines()[-1][:80]}")
    url = f"http://127.0.0.1:{req.port}"
    # 等服务就绪
    for _ in range(30):
        ok, _ = await _probe(url, "__none__", timeout=2)
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as c:
                await c.get(f"{url}/api/tags")
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1)
    await _event(r, "INFO", f"embed ▸ 拉取模型 {req.model}（数 GB，需数分钟）…")
    rc, out = await _local_run(f'ollama pull {req.model}', timeout=1800)
    if rc != 0:
        raise RuntimeError(f"ollama pull 失败：{out[-200:]}")
    return url


# ---------- API ----------
@router.get("/status")
async def status(r=Depends(deps.get)) -> dict:
    cfg = await r.hgetall(CONFIG_KEY)
    url = cfg.get("embedding_url", "")
    model = cfg.get("embedding_model", "bge-m3")
    ok, note = (False, "未配置")
    if url:
        ok, note = await _probe(url, model)
    return {"url": url, "model": model, "ok": ok, "note": note,
            "deploying": _state["running"], "step": _state["step"],
            "deploy_error": _state["error"]}


@router.post("/deploy", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def deploy(req: DeployIn, r=Depends(deps.get)) -> dict:
    if _state["running"]:
        raise HTTPException(409, "已有部署任务进行中")
    _state.update(running=True, target=req.target, step="启动", error="")

    async def job() -> None:
        try:
            if req.target == "local":
                url = await _deploy_local(r, req)
            else:
                url = await _deploy_node(r, req.target, req)
            _state["step"] = "健康检查"
            ok, note = await _probe(url, req.model, timeout=30)
            if not ok:
                raise RuntimeError(f"部署后健康检查失败：{note}")
            await r.hset(CONFIG_KEY, mapping={
                "embedding_url": url, "embedding_model": req.model})
            _state["step"] = "完成"
            await _event(r, "OK", f"embed ▸ 语义引擎已接入：{url}（模型 {req.model}）")
        except Exception as e:  # noqa: BLE001
            _state["error"] = str(e)[:300]
            _state["step"] = "失败"
            await _event(r, "ERR", f"embed ▸ 部署失败：{str(e)[:200]}")
        finally:
            _state["running"] = False

    asyncio.create_task(job())
    return {"accepted": True, "target": req.target,
            "note": "部署在后台执行，进度见集群事件流 / GET status 轮询"}
