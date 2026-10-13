# 推理节点 Agent：双架构镜像（x86 服务器 + ARM64 Jetson 共用一份 Dockerfile）
# python:3.11-slim 官方多架构基础镜像；GPU 采集层在运行期按平台自动降级。
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Docker CLI（静态二进制，服务管控走宿主 docker.sock；双架构按 TARGETARCH 选包）
ARG TARGETARCH
ARG DOCKER_VERSION=27.5.1
ARG DOCKER_MIRROR=https://mirrors.aliyun.com/docker-ce/linux/static/stable
RUN set -e; \
    ARCH=$([ "${TARGETARCH:-amd64}" = "arm64" ] && echo aarch64 || echo x86_64); \
    apt-get update && apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL "${DOCKER_MIRROR}/${ARCH}/docker-${DOCKER_VERSION}.tgz" | tar xz --strip-components=1 -C /usr/local/bin docker/docker; \
    docker --version; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agent
COPY requirements.txt .
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

COPY app ./app
EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
