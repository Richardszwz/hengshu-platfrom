# 衡枢路由引擎 HSE（多架构：amd64/arm64 全节点可跑，无状态）
# 构建上下文=仓库根（libs/hslicense 授权校验库随包）
FROM python:3.11-slim
WORKDIR /opt/hse
COPY hse/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs ./libs
COPY hse ./hse
EXPOSE 9100
ENV REDIS_URL=redis://127.0.0.1:6379/0
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:9100/healthz')"
CMD ["uvicorn", "hse.app.main:app", "--host", "0.0.0.0", "--port", "9100"]
