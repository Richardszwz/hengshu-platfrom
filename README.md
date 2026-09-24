# 衡枢 HENGSHU · 多机推理 API 负载均衡器

> 万流归一，衡以分之。GPU 集群运维平台 + API 入口。

负载均衡（衡）+ 调度枢纽（枢）：多机多模型推理集群的统一纳管、智能调度、
OpenAI 兼容 API 网关、会话保持、多租户隔离。

## 仓库结构

```
hengshu/
├── console/                # Web 控制台（Vue3 + ElementPlus 前端 / FastAPI 后端）
│   ├── backend/            #   控制台 API：认证、节点、服务、租户、规则、仪表盘
│   └── frontend/           #   前端 SPA
├── router/                 # 数据面 LB：OpenAI 兼容透传、负载评分调度、会话粘性
│   └── app/
│       ├── balancer.py     #   评分：0.35·GPU + 0.25·VRAM + 0.25·队列 + 0.15·TTFT，粘性 ×0.7
│       ├── registry.py     #   模型实例注册表（Redis）
│       └── session.py      #   会话绑定 + 历史重放
├── agent/                  # GPU 节点 Agent：心跳、指标采集（vLLM/TGI/TRT-LLM/Ollama 插件）、服务启停
│   └── app/collectors/     #   四框架指标采集插件
├── scripts/mock_agent.py   # 模拟 4 台 GPU 节点（无真机时的全链路演示/开发）
├── docs/                   # 方案书 + 架构图
└── .github/workflows/ci.yml
```

## 快速开始（开发机，无需 GPU 真机）

```bash
cp .env.example .env
# 生成 admin 密码哈希并写入 .env
python scripts/gen_admin_hash.py '你的密码'

docker compose up -d redis
docker compose --profile demo up -d        # console + router + 4 台模拟节点

# 控制台  http://localhost:8080   (admin / 你的密码)
# API 网关 http://localhost:9000/v1/chat/completions
```

无 Docker 的纯本机开发：

```bash
# 终端1 Redis（需本机 redis-server 或 wsl）
# 终端2 模拟节点 ×4
for n in 0 1 2 3; do python scripts/mock_agent.py --node gpu-node-$((97+n)) --port $((9100+n)) & done
# 终端3 控制台后端
cd console/backend && pip install -r requirements.txt && uvicorn app.main:app --port 8080
# 终端4 Router
cd router && pip install -r requirements.txt && uvicorn app.main:app --port 9000
# 终端5 前端（Vite 热更）
cd console/frontend && npm i && npm run dev
```

## 冒烟验证

```bash
# 1. 登录拿 token
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"你的密码"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. 控制台看到 4 台模拟节点 + 6 个模型服务
curl -s localhost:8080/api/nodes -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -30
curl -s localhost:8080/api/services -H "Authorization: Bearer $TOKEN"

# 3. 网关路由（多 session 续聊验证粘性）
curl -s localhost:9000/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "Qwen3-30B-A3B",
  "messages": [{"role":"user","content":"你好"}],
  "session_id": "demo-sess-1"
}'
curl -s localhost:9000/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "Qwen3-30B-A3B",
  "messages": [{"role":"user","content":"接着上面"}],
  "session_id": "demo-sess-1"
}'   # 两次应命中同一实例
```

## 测试

```bash
cd router && python -m pytest app/tests -q          # 调度器/粘性单测
cd console/backend && python -m pytest tests -q     # 控制台冒烟
```

## 路线图

P0 脚手架（本仓库）→ P1 纳管+MVP → P2 智能调度+服务管控+多租户 →
P3 高可用+二次开发 → P4 多机并行单模型 → P5 离线交付。
详见 `docs/proposal.md`（v1.6 定稿）。
