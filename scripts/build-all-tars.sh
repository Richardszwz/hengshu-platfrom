#!/usr/bin/env bash
# 一键构建全组件双架构气隙交付 tar：agent / router / console / hse
# 用法: bash scripts/build-all-tars.sh   （单个组件可用 --only agent|router|console|hse）
# 产物: dist/<组件>-<x86_64|aarch64>.tar
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist

ONLY=""
[ "${1:-}" = "--only" ] && ONLY="${2:-}"

# 组件:构建上下文（hse 需要仓库根带 libs/；image-server 带 extras/；其余 context=自身目录）
declare -A CTX=( [agent]=agent [router]=router [console]=console [hse]=. [image-server]=. )
declare -A DFILE=( [agent]=agent/Dockerfile [router]=router/Dockerfile [console]=console/Dockerfile [hse]=hse/Dockerfile [image-server]=extras/image_server/Dockerfile )

for comp in agent router console hse image-server; do
  [ -n "$ONLY" ] && [ "$comp" != "$ONLY" ] && continue
  for arch in amd64 arm64; do
    suffix=$arch; [ "$arch" = amd64 ] && suffix=x86_64; [ "$arch" = arm64 ] && suffix=aarch64
    echo "==> $comp linux/$arch → dist/$comp-$suffix.tar"
    # image-server：arm64 基础镜像不同（Blackwell/Jetson 需 NGC pytorch，CUDA 12.8+）
    EXTRA_ARGS=""
    if [ "$comp" = image-server ] && [ "$arch" = arm64 ]; then
      EXTRA_ARGS="--build-arg BASE_IMAGE=nvcr.io/nvidia/pytorch:25.09-py3"
    fi
    docker buildx build --platform "linux/$arch" \
      -f "${DFILE[$comp]}" -t "hengshu/$comp:latest" \
      --output "type=docker,dest=dist/$comp-$suffix.tar" \
      $EXTRA_ARGS \
      ${BASE_IMAGE:+--build-arg BASE_IMAGE=$BASE_IMAGE} \
      "${CTX[$comp]}"
  done
done

ls -la dist/
echo "OK：气隙环境依次 docker load < dist/<comp>-<arch>.tar 即可"
