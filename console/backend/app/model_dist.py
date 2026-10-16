"""模型自动分发：统一 /models 约定下的 ensure_model 编排。

规则：目标节点已有 → 直接用；没有 → 找有的节点内网 tar 流拷贝（经控制台中转，
两端无需互信）；都没有 → 目标节点在线拉取（modelscope，Agent 容器内执行）。

全程写 hs:events（集群事件流）+ hs:nodelog:{node}（节点系统日志）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import asyncssh
import httpx

from ..crypto import decrypt
from ..keys import CRED_PREFIX, NODE_PREFIX

log = logging.getLogger("hengshu.model_dist")

EVENTS_KEY = "hs:events"


def models_root(conn: dict) -> str:
    """节点宿主机模型根目录：models_mount（如 /home/user/models:/models:ro）优先，
    缺省按 SSH 用户推导 /home/<user>/models。"""
    mount = conn.get("models_mount", "")
    if mount:
        return mount.split(":", 1)[0]
    user = conn.get("username", "root")
    return "/root/models" if user == "root" else f"/home/{user}/models"


async def _emit(r, level: str, msg: str, node: str | None = None) -> None:
    item = json.dumps({"ts": round(time.time(), 3), "level": level, "msg": msg},
                      ensure_ascii=False)
    pipe = r.pipeline()
    pipe.rpush(EVENTS_KEY, item)
    pipe.ltrim(EVENTS_KEY, -200, -1)
    if node:
        pipe.rpush(f"hs:nodelog:{node}", item)
        pipe.ltrim(f"hs:nodelog:{node}", -200, -1)
    await pipe.execute()


async def _agent_addr(r, nid: str) -> str:
    h = await r.hgetall(f"{NODE_PREFIX}{nid}")
    conn = await r.hgetall(f"hs:conn:{nid}")
    return (f"http://{conn['host']}:9100" if conn.get("host")
            else h.get("agent_addr", ""))


async def node_models(r, nid: str) -> list[str]:
    """节点现有模型目录名列表（Agent 一次性容器列宿主机目录）。"""
    addr = await _agent_addr(r, nid)
    conn = await r.hgetall(f"hs:conn:{nid}")
    if not addr:
        return []
    try:
        async with httpx.AsyncClient(timeout=70.0, trust_env=False) as c:
            d = (await c.post(f"{addr}/models/list",
                              json={"host_dir": models_root(conn)})).json()
            return [m["name"] for m in d.get("models", [])]
    except Exception as e:  # noqa: BLE001
        log.warning("node_models %s failed: %s", nid, e)
        return []


async def inventory(r) -> dict[str, list[str]]:
    """全在线节点模型清单（并行）。"""
    keys = await r.keys(f"{NODE_PREFIX}*")
    nids = []
    for k in keys:
        h = await r.hgetall(k)
        if int(h.get("last_seen", "0")) > time.time() - 15:
            nids.append(k.removeprefix(NODE_PREFIX))
    out = await asyncio.gather(*(node_models(r, n) for n in nids))
    return dict(zip(nids, out))


async def _cred(r, nid: str) -> tuple[str, int, str, dict]:
    conn = await r.hgetall(f"hs:conn:{nid}")
    blob = await r.get(f"{CRED_PREFIX}{nid}")
    if not conn.get("host") or not blob:
        raise RuntimeError(f"节点 {nid} 无连接信息/凭证")
    secret = decrypt(blob)
    kw: dict = {"known_hosts": None, "login_timeout": 15}
    if secret.startswith("-----BEGIN"):
        kw["client_keys"] = [asyncssh.import_private_key(secret)]
    else:
        kw["password"] = secret
    return conn["host"], int(conn.get("port", "22")), conn.get("username", "root"), kw


async def copy_between_nodes(r, src: str, dst: str, model: str) -> None:
    """内网 tar 流拷贝（经控制台中转）：src:models_root/model → dst:models_root/model。"""
    sh, sp, su, skw = await _cred(r, src)
    dh, dp, du, dkw = await _cred(r, dst)
    sdir = models_root(await r.hgetall(f"hs:conn:{src}"))
    ddir = models_root(await r.hgetall(f"hs:conn:{dst}"))
    async with asyncssh.connect(sh, port=sp, username=su, **skw) as s, \
               asyncssh.connect(dh, port=dp, username=du, **dkw) as d:
        sp_ = await s.create_process(f"tar cf - -C {sdir} {model}", encoding=None)
        dp_ = await d.create_process(f"mkdir -p {ddir} && tar xf - -C {ddir}",
                                     encoding=None)
        total = 0
        last = time.time()
        while True:
            chunk = await sp_.stdout.read(1 << 20)
            if not chunk:
                break
            dp_.stdin.write(chunk)
            await dp_.stdin.drain()
            total += len(chunk)
            if time.time() - last > 20:
                await _emit(r, "INFO",
                            f"model ▸ {model} 拷贝中 {src}→{dst} 已 {total / 2**30:.1f} GiB",
                            node=dst)
                last = time.time()
        dp_.stdin.write_eof()
        src_rc = (await sp_.wait(check=False)).exit_status
        dst_rc = (await dp_.wait(check=False)).exit_status
        if src_rc or dst_rc:
            raise RuntimeError(f"tar 流拷贝失败 src_rc={src_rc} dst_rc={dst_rc}")


async def ensure_model(r, node: str, model: str, repo: str = "") -> str:
    """确保 node 上有 model；返回来源：local / copy:<src> / download。异常向上抛。"""
    have = await node_models(r, node)
    if model in have:
        return "local"
    # 他机查找
    inv = await inventory(r)
    src = next((n for n, ms in inv.items() if n != node and model in ms), None)
    if src:
        await _emit(r, "INFO",
                    f"model ▸ {node} 缺 {model}，从 {src} 内网拷贝开始", node=node)
        await copy_between_nodes(r, src, node, model)
        await _emit(r, "OK", f"model ▸ {model} 拷贝完成 {src} → {node}", node=node)
        return f"copy:{src}"
    # 在线拉取
    repo = repo or (model if "/" in model else f"Qwen/{model}")
    addr = await _agent_addr(r, node)
    conn = await r.hgetall(f"hs:conn:{node}")
    if not addr:
        raise RuntimeError(f"节点 {node} Agent 不可达，无法在线拉取")
    await _emit(r, "INFO",
                f"model ▸ 集群无 {model}，{node} 在线拉取 {repo}（大模型需数十分钟）",
                node=node)
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as c:
        resp = await c.post(f"{addr}/models/download",
                            json={"repo": repo, "host_dir": models_root(conn),
                                  "name": model})
        if resp.status_code != 200:
            raise RuntimeError(f"在线拉取受理失败: {resp.text[:200]}")
    return "download"
