"""服务镜像库：INSTALL_TAR_DIR 下的镜像 tar 管理 + 推送节点装载。

扫描 tar 的 manifest.json 解析 RepoTags 建立 镜像名→文件 映射（含架构）。
启动服务镜像缺失时由 services.py 调用 push_image 自动分发，新节点纳管可选预置推送。
"""
from __future__ import annotations

import asyncio
import io
import json
import tarfile
import time
from pathlib import Path

import httpx

from . import deps
from .config import settings


class ImageEntry:
    def __init__(self, tar_path: Path, tag: str, arch: str) -> None:
        self.tar_path = tar_path
        self.tag = tag          # 如 hengshu/image-server:latest-arm64
        self.arch = arch        # amd64 / arm64
        self.size = tar_path.stat().st_size
        self.mtime = tar_path.stat().st_mtime


_cache: dict[str, ImageEntry] = {}
_cache_at = 0.0
# 传输中去重锁（node:tag）——防手动+自动并发重复传大文件
_pushing: set[str] = set()


def _scan() -> dict[str, ImageEntry]:
    """扫描 tar 目录解析镜像清单（tar manifest.json + config 的 architecture）。"""
    global _cache, _cache_at
    d = Path(settings.install_tar_dir)
    if not d.exists():
        return {}
    # 目录内容变化才重扫（文件多时逐个开 tar 有成本）
    newest = max((f.stat().st_mtime for f in d.glob("*.tar")), default=0)
    if _cache and time.time() - _cache_at < 60 and newest <= _cache_at:
        return _cache
    out: dict[str, ImageEntry] = {}
    for tar_path in d.glob("*.tar"):
        try:
            with tarfile.open(tar_path) as tf:
                mf = json.load(tf.extractfile("manifest.json"))
                cfg_name = mf[0]["Config"]
                cfg = json.load(tf.extractfile(cfg_name))
                arch = cfg.get("architecture", "")
                for tag in mf[0].get("RepoTags", []):
                    out[tag] = ImageEntry(tar_path, tag, arch)
        except Exception:  # noqa: BLE001  # 非镜像 tar（如 agent 包是镜像 tar，无关文件跳过）
            continue
    _cache, _cache_at = out, time.time()
    return out


def list_images() -> list[dict]:
    return [{"tag": e.tag, "arch": e.arch, "size_mb": round(e.size / 2**20),
             "file": e.tar_path.name}
            for e in sorted(_scan().values(), key=lambda x: x.tag)]


def find_tar(image: str, arch: str = "") -> ImageEntry | None:
    """按镜像名找 tar；name 不带 tag 时模糊匹配 name:*。arch 优先匹配目标架构。"""
    lib = _scan()
    if image in lib:
        return lib[image]
    cands = [e for tag, e in lib.items()
             if tag.split(":")[0] == image or tag.split(":")[0].endswith("/" + image)]
    if not cands:
        # tag 前缀匹配（hengshu/image-server:latest-arm64 vs image-server）
        cands = [e for tag, e in lib.items() if image in tag]
    if arch:
        arch_cands = [e for e in cands if e.arch == arch]
        if arch_cands:
            return arch_cands[0]
    return cands[0] if cands else None


async def push_image(r, node_id: str, image: str) -> dict:
    """把镜像 tar 流式推到节点 agent 装载；轮询确认存在。返回装载结果。"""
    from .api.nodes import NODE_PREFIX  # 延迟导入防环
    lock_key = f"{node_id}:{image}"
    if lock_key in _pushing:
        return {"ok": False, "note": "同镜像推送该节点进行中，跳过重复"}
    _pushing.add(lock_key)
    try:
        conn = await r.hgetall(f"hs:conn:{node_id}")
        node = await r.hgetall(f"{NODE_PREFIX}{node_id}")
        arch = node.get("arch", "")
        if arch in ("x86_64", "amd64"):
            arch = "amd64"
        elif arch in ("aarch64", "arm64"):
            arch = "arm64"
        entry = find_tar(image, arch)
        if not entry:
            raise RuntimeError(f"镜像库无 {image}（{arch or '任意架构'}）——"
                               f"把对应 tar 放入 {settings.install_tar_dir}")
        addr = f"http://{conn['host']}:9100" if conn.get("host") else node.get("agent_addr", "")
        if not addr:
            raise RuntimeError(f"节点 {node_id} agent 地址未知")

        async def _emit(level: str, msg: str) -> None:
            pipe = r.pipeline()
            pipe.rpush("hs:events", json.dumps(
                {"ts": round(time.time(), 3), "level": level, "msg": msg}, ensure_ascii=False))
            pipe.ltrim("hs:events", -500, -1)
            await pipe.execute()

        await _emit("INFO", f"image ▸ 镜像库推送 {entry.tag}（{entry.size / 2**30:.1f} GiB）→ {node_id}")
        # 流式上传（读文件边发，不全量进内存）
        async def stream():
            with open(entry.tar_path, "rb") as f:
                while chunk := f.read(4 * 1024 * 1024):
                    yield chunk
        async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0, read=60.0),
                                     trust_env=False) as c:
            resp = await c.post(f"{addr}/images/load",
                                params={"size": entry.size, "tag": entry.tag},
                                content=stream())
            if resp.status_code != 200:
                raise RuntimeError(f"agent 受理失败: {resp.text[:200]}")
        # 轮询装载完成（大镜像 load 需数分钟）
        deadline = time.time() + 1800
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
            while time.time() < deadline:
                await asyncio.sleep(8)
                try:
                    d = (await c.post(f"{addr}/images/has", json={"image": entry.tag})).json()
                    if d.get("present"):
                        await _emit("OK", f"image ▸ {node_id} 已装载 {entry.tag}")
                        return {"ok": True, "tag": entry.tag, "node": node_id}
                except Exception:  # noqa: BLE001
                    continue
        raise RuntimeError(f"装载确认超时（{entry.tag} → {node_id}），看节点系统日志")
    finally:
        _pushing.discard(lock_key)
