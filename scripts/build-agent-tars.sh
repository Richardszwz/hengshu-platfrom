#!/usr/bin/env bash
# 构建双架构 Agent 气隙交付 tar（installer 按 uname -m 选 agent-x86_64.tar / agent-aarch64.tar）
# 用法: bash scripts/build-agent-tars.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist

for arch in amd64 arm64; do
  echo "==> 构建 linux/$arch"
  # docker.io 不通时：BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim bash scripts/build-agent-tars.sh
  docker buildx build --platform "linux/$arch" -t hengshu/agent:latest \
    ${BASE_IMAGE:+--build-arg BASE_IMAGE=$BASE_IMAGE} \
    --output "type=docker,dest=dist/agent-${arch/amd64/x86_64}.tar" .
done

ls -la dist/
echo "OK: 上传 dist/agent-*.tar 到控制台机器的 \$INSTALL_TAR_DIR（默认 ./dist）即可气隙安装"
