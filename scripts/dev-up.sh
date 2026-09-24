#!/usr/bin/env bash
# 开发机全链路演示（无 Docker / 无 GPU）：fakeredis + mock 节点×4 + 控制台 + Router
# 用法：bash scripts/dev-up.sh        # 前台运行，Ctrl+C 全停
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/Scripts/python
export ADMIN_PASSWORD_HASH=${ADMIN_PASSWORD_HASH:-$($PY scripts/gen_admin_hash.py hengshu-dev)}
export REDIS_URL=redis://127.0.0.1:6379/0

cleanup() { kill $(jobs -p) 2>/dev/null || true; }
trap cleanup EXIT

echo "==> [1/4] fakeredis (TCP :6379)"
$PY -c "
from fakeredis import TcpFakeServer
srv = TcpFakeServer(('127.0.0.1', 6379), server_type='redis')
srv.serve_forever()
" &

echo "==> [2/4] mock agent ×4 (心跳 + 推理端点 8001-8004)"
$PY scripts/mock_agent.py --serve &

echo "==> [3/4] console :8080 (admin / hengshu-dev)"
(cd console/backend && ../../$PY -m uvicorn app.main:app --port 8080) &

echo "==> [4/4] router :9000 (API Key: dev-key)"
(cd router && ../$PY -m uvicorn app.main:app --port 9000) &

sleep 5
echo ""
echo "控制台:  http://localhost:8080   (admin / hengshu-dev)"
echo "API 入口: http://localhost:9000/v1/models  (Header: Authorization: Bearer demo-api-key)"
echo "测试:     curl -X POST http://localhost:9000/v1/chat/completions -H 'Authorization: Bearer demo-api-key' -H 'X-Session-Id: demo-1' -H 'Content-Type: application/json' -d '{\"model\":\"qwen3-30b\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"stream\":true}'"
wait
