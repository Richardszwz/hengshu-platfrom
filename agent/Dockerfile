# 推理节点 Agent：双架构镜像（x86 服务器 + ARM64 Jetson 共用一份 Dockerfile）
# python:3.11-slim 官方多架构基础镜像；GPU 采集层在运行期按平台自动降级。
FROM python:3.11-slim

WORKDIR /opt/agent
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
