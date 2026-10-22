# 衡枢图像生成服务 wrapper（diffusers → OpenAI 兼容）

适用：Qwen-Image / SD3 / FLUX 等 diffusers 格式模型（目录含 `model_index.json`）。

## 在衡枢平台启动（S3 等 Spark/GB10 节点示例）

启动对话框填写：

| 字段 | 值 |
|---|---|
| 服务类型 | **图像生成** |
| 镜像 | `nvcr.io/nvidia/pytorch:25.09-py3`（ARM64/GB10 兼容，torch+CUDA 自带；x86 节点可用 `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`） |
| 启动命令 | 见下 |
| 服务端口 | `8100` |
| 卷挂载 | `/home/xux/models:/models:ro` + **wrapper 脚本挂载**（见下） |

启动命令（容器内先装依赖再起服务）：

```bash
pip install -q fastapi uvicorn "diffusers>=0.35" transformers accelerate safetensors && python /opt/hs-extra/server.py --model /models/Qwen-Image-2.1 --port {port}
```

wrapper 脚本进容器的方式：把本目录 `server.py` 拷到节点任一目录（如 `/home/xux/models/_extras/server.py`），卷挂载追加一行：

```
/home/xux/models/_extras:/opt/hs-extra:ro
```

## 验证

```bash
curl http://<平台IP>:9000/v1/images/generations \
  -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen-Image-2.1","prompt":"一只赛博朋克风格的猫","size":"1024x1024"}'
# 返回 data[0].b64_json → base64 解码即 PNG
```

## 备注

- Qwen-Image 为 20B MMDiT，bf16 显存约 40GB；GB10（128GB 统一内存）可跑；24GB 卡请换小模型或开更激进 offload
- 模型目录必须是 diffusers 格式（`model_index.json` + transformer/vae/text_encoder 子目录）
- 首次加载需 1-3 分钟（权重读盘+CUDA 初始化），巡检期内属正常
