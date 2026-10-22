#!/usr/bin/env python3
"""衡枢图像生成服务 wrapper：diffusers → OpenAI /v1/images/generations 兼容。

用法（容器内）：
  pip install fastapi uvicorn "diffusers>=0.35" transformers accelerate safetensors
  python server.py --model /models/Qwen-Image --port 8100

配合衡枢启动对话框：服务类型=图像生成；启动命令指向本脚本。
"""
from __future__ import annotations

import argparse
import base64
import io
import time

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="hengshu-image-server")
_pipe = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


class ImageReq(BaseModel):
    prompt: str
    model: str = ""
    n: int = 1
    size: str | None = None          # "1024x1024"
    response_format: str = "b64_json"
    negative_prompt: str = ""
    steps: int | None = None
    seed: int | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": _pipe is not None, "device": _device}


def _load(model_path: str) -> None:
    global _pipe
    from diffusers import QwenImagePipeline
    _pipe = QwenImagePipeline.from_pretrained(model_path, torch_dtype=torch.bfloat16)
    _pipe.to(_device)
    try:
        _pipe.enable_attention_slicing()   # 显存紧张时切片注意力
    except Exception:
        pass


@app.post("/v1/images/generations")
def generations(req: ImageReq) -> JSONResponse:
    if _pipe is None:
        return JSONResponse({"error": "model not loaded"}, status_code=503)
    w, h = 1024, 1024
    if req.size and "x" in req.size:
        w, h = (int(x) for x in req.size.split("x", 1))
    gen = torch.Generator(device=_device).manual_seed(req.seed) if req.seed is not None else None
    out = _pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        num_inference_steps=req.steps or 50,
        width=w, height=h,
        num_images_per_prompt=min(req.n, 4),
        generator=gen,
    )
    data = []
    for img in out.images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data.append({"b64_json": base64.b64encode(buf.getvalue()).decode()})
    return JSONResponse({"created": int(time.time()), "data": data})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="diffusers 模型目录（含 model_index.json）")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()
    _load(args.model)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
