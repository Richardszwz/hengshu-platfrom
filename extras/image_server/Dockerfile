# 衡枢图像生成服务镜像：diffusers 环境预置，启动命令纯净
# 构建（仓库根）：
#   amd64: docker buildx build --platform linux/amd64 -f extras/image_server/Dockerfile \
#            --build-arg BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime \
#            --output type=docker,dest=dist/image-server-x86_64.tar .
#   arm64(GB10/Jetson): BASE_IMAGE=nvcr.io/nvidia/pytorch:25.09-py3（Blackwell 需 CUDA 12.8+）
ARG BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
FROM ${BASE_IMAGE}

# pypi 直连不稳 → 默认走阿里云镜像（可 --build-arg PIP_INDEX_URL 覆盖）
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} fastapi "uvicorn[standard]" \
    "diffusers>=0.35" transformers accelerate safetensors

# NGC 容器预装的训练/微调生态包与 diffusers 0.41 import 链冲突（推理用不到，全卸）：
# - torchao 0.13+git：FqnToConfig 缺失炸 lora_pipeline
# - peft 旧版：ScalingType 从 torch.nn.functional import 不到，炸 auto_pipeline
RUN pip uninstall -y torchao peft || true

COPY extras/image_server/server.py /opt/hs-extra/server.py

# 构建期自检（目标架构下真跑，QEMU 慢但可靠）：wrapper 与管线必须能 import，
# 挂了构建直接失败——坏镜像不出构建管线（torchao/peft 类预装冲突在此现形）
RUN python -c "import fastapi, uvicorn, torch, diffusers, transformers; \
    from diffusers import AutoPipelineForText2Image, QwenImagePipeline; \
    import diffusers; assert hasattr(diffusers, 'QwenImage21Pipeline'), 'diffusers 无 QwenImage21Pipeline（2.1 模型需要）'; \
    import sys; sys.path.insert(0, '/opt/hs-extra'); \
    import server; print('image-server selfcheck OK', torch.__version__, diffusers.__version__)"

EXPOSE 8100
# 入口即服务，启动命令只剩模型路径与端口参数
ENTRYPOINT ["python", "/opt/hs-extra/server.py"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=10 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8100/health')"
