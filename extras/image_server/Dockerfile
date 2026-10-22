# 衡枢图像生成服务镜像：diffusers 环境预置，启动命令纯净
# 构建（仓库根）：
#   amd64: docker buildx build --platform linux/amd64 -f extras/image_server/Dockerfile \
#            --build-arg BASE_IMAGE=pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime \
#            --output type=docker,dest=dist/image-server-x86_64.tar .
#   arm64(GB10/Jetson): BASE_IMAGE=nvcr.io/nvidia/pytorch:25.09-py3（Blackwell 需 CUDA 12.8+）
ARG BASE_IMAGE=pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
FROM ${BASE_IMAGE}

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" \
    "diffusers>=0.35" transformers accelerate safetensors

COPY extras/image_server/server.py /opt/hs-extra/server.py

EXPOSE 8100
# 入口即服务，启动命令只剩模型路径与端口参数
ENTRYPOINT ["python", "/opt/hs-extra/server.py"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=10 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8100/health')"
