# 衡枢图像生成服务（预置镜像）

适用：Qwen-Image / SD3 / FLUX 等 diffusers 格式模型（目录含 `model_index.json`）。

## 镜像（环境已预置，启动命令干净）

| 架构 | 基础镜像 | 产物 |
|---|---|---|
| x86_64 | docker.m.daocloud.io/pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime（docker.io 不通时走镜像站前缀） | `dist/image-server-x86_64.tar` |
| aarch64（GB10/Jetson） | nvcr.io/nvidia/pytorch:25.09-py3（Blackwell 需 CUDA 12.8+） | `dist/image-server-aarch64.tar` |

预装：fastapi / uvicorn / diffusers≥0.35 / transformers / accelerate / safetensors + wrapper（`/opt/hs-extra/server.py`）。

## 在衡枢平台启动

启动对话框填写：

| 字段 | 值 |
|---|---|
| 服务类型 | **图像生成** |
| 镜像 | `hengshu/image-server:latest`（docker load 后的镜像名） |
| 启动命令 | `python /opt/hs-extra/server.py --model /models/Qwen-Image-2.1 --port {port}` |
| 服务端口 | `8100` |
| 卷挂载 | `/home/xux/models:/models:ro`（仅此一行） |

**就这么多**——依赖已固进镜像，启动命令只描述「加载哪个模型、听哪个端口」。

## 气隙分发

`image-server-<arch>.tar` 与其他组件 tar 一样放 `INSTALL_TAR_DIR`，节点上 `docker load` 一次即可。

## 验证

```bash
curl http://<平台IP>:9000/v1/images/generations \
  -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen-Image-2.1","prompt":"一只赛博朋克风格的猫","size":"1024x1024"}'
# 返回 data[0].b64_json → base64 解码即 PNG
```

## 备注

- Qwen-Image 为 20B MMDiT，bf16 显存约 40GB；GB10（128GB 统一内存）可跑；24GB 卡请换小模型
- 模型目录必须是 diffusers 格式（`model_index.json` + transformer/vae/text_encoder 子目录）
- 首次加载 1-3 分钟属正常（镜像 HEALTHCHECK start-period 已放宽到 300s）
- 自定义图像框架不用本镜像也行：服务类型选「自定义框架」，直连节点端口访问
