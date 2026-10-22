"""镜像库 API：清单查询 + 手动/自动推送节点装载。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from .. import deps, image_registry
from ..license_guard import require_active_base
from ..security import require_role

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("")
async def list_lib() -> dict:
    return {"images": image_registry.list_images(),
            "tar_dir": image_registry.settings.install_tar_dir}


@router.post("/push/{node_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def push(node_id: str, image: str, r=Depends(deps.get)) -> dict:
    """手动推送镜像到节点（后台执行，进度/结果进事件流）。"""
    entry = image_registry.find_tar(image)
    if not entry:
        raise HTTPException(404, f"镜像库无 {image}（tar 目录: {image_registry.settings.install_tar_dir}）")

    import asyncio

    async def _bg() -> None:
        try:
            await image_registry.push_image(r, node_id, image)
        except Exception as e:
            pipe = r.pipeline()
            import json as _j
            pipe.rpush("hs:events", _j.dumps(
                {"ts": round(time.time(), 3), "level": "ERR",
                 "msg": f"image ▸ 推送失败 {image} → {node_id}: {e}"}, ensure_ascii=False))
            pipe.ltrim("hs:events", -500, -1)
            await pipe.execute()

    t = asyncio.create_task(_bg())
    image_registry._BG = getattr(image_registry, "_BG", set())
    image_registry._BG.add(t)
    t.add_done_callback(image_registry._BG.discard)
    return {"accepted": image, "node": node_id,
            "note": f"推送中（{entry.size / 2**30:.1f} GiB，进度见事件流）"}


async def auto_seed_node(r, node_id: str) -> None:
    """新节点纳管成功后自动预置平台自研镜像（image-server 等 hengshu/* 自研件）。

    按节点架构过滤（arm64 节点只推 arm64 tar）；公网可拉的（vllm/llama.cpp）不推。
    失败不阻塞纳管。
    """
    from .nodes import NODE_PREFIX
    node = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    arch = node.get("arch", "")
    want = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(arch, "")
    lib = image_registry._scan()
    seeds = [e for tag, e in lib.items()
             if tag.startswith("hengshu/") and "agent" not in tag
             and (not want or e.arch == want)]
    for e in seeds:
        try:
            await image_registry.push_image(r, node_id, e.tag)
        except Exception:
            pass  # 事件流已有记录
