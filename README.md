# 衡枢 HENGSHU · 多机推理 API 负载均衡器

> 万流归一，衡以分之。GPU 集群运维平台 + API 入口。

负载均衡（衡）+ 调度枢纽（枢）：多机多模型推理集群的统一纳管、智能调度、
OpenAI 兼容 API 网关、会话保持、多租户隔离。

## 异构推理节点（x86 + ARM64）

推理节点支持混合架构部署：

| 节点类型 | 架构 | 典型机型 | GPU 指标来源 |
|---|---|---|---|
| x86 服务器 | amd64 | L4 / RTX 6000 / L40S / H100 PCIe | pynvml |
| 边缘盒 | arm64 | Jetson AGX Orin / Orin Nano | jtop(tegrastats)，P1 实装；接口已留 |

- 全部镜像双架构：`docker buildx build --platform linux/amd64,linux/arm64`（CI 已验证）
- Agent 的 GPU 采集层运行期按 `platform.machine()` 自动降级，采集失败不拖垮心跳
- 控制台/管理节点若也是 ARM 机器（如 DGX Spark），同一镜像直接跑

## 仓库结构

```
hengshu/
├── console/                # Web 控制台（Vue3 + ElementPlus 前端 / FastAPI 后端）
│   ├── backend/            #   控制台 API：认证、节点、服务、租户、规则、仪表盘
│   └── frontend/           #   前端 SPA（HUD 深色风，ECharts）
├── router/                 # 数据面 LB：OpenAI 兼容透传、负载评分调度、会话粘性
│   └── app/
│       ├── balancer.py     #   评分：0.35·GPU + 0.25·VRAM + 0.25·队列 + 0.15·TTFT，粘性 ÷0.7
│       ├── registry.py     #   模型实例注册表（Redis）
│       └── session.py      #   会话绑定 + 历史重放
├── agent/                  # GPU 节点 Agent：心跳、指标采集（vLLM/TGI/TRT-LLM/Ollama 插件）、服务启停
│   └── app/collectors/     #   四框架指标采集插件 + GPU 主机采集（架构感知）
├── scripts/
│   ├── mock_agent.py       # 模拟 4 台异构节点（无真机时的全链路演示/开发）
│   ├── dev-up.sh           # 无 Docker 一键全栈（fakeredis + mock + console + router）
│   └── gen_admin_hash.py   # admin 密码 argon2 哈希生成
├── docs/                   # 方案书 v1.6 + 架构图 + 仪表盘截图
└── .github/workflows/ci.yml
```

## 快速开始

### 方式一：无 Docker 一键全栈（开发机已验证，Windows git-bash 可跑）

```bash
python -m venv .venv && .venv/Scripts/pip install \
  -r router/requirements.txt -r console/backend/requirements.txt \
  -r scripts/requirements.txt
bash scripts/dev-up.sh
# 控制台  http://localhost:8080   (admin / hengshu-dev)
# Router  http://localhost:9000   (Authorization: Bearer demo-api-key)
```

### 方式二：Docker Compose

```bash
cp .env.example .env
# .env 写入 ADMIN_PASSWORD_HASH（python scripts/gen_admin_hash.py '你的密码'）
docker compose up -d redis
docker compose --profile demo up -d        # console + router + 4 台模拟节点
```

## 冒烟验证

```bash
# 1. 登录拿 token
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"hengshu-dev"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. 控制台看到 4 台异构节点（2×x86_64 + 2×aarch64）+ 4 个模型服务
curl -s localhost:8080/api/nodes -H "Authorization: Bearer $TOKEN" | python -m json.tool
curl -s localhost:8080/api/dashboard/summary -H "Authorization: Bearer $TOKEN" | python -m json.tool

# 3. 网关路由（同一 X-Session-Id 连发两次应命中同一实例，Redis hs:sess:{sid} 可查绑定）
for i in 1 2; do curl -sN -X POST localhost:9000/v1/chat/completions \
  -H 'Authorization: Bearer demo-api-key' -H 'X-Session-Id: sticky-test' \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"qwen3-30b\",\"messages\":[{\"role\":\"user\",\"content\":\"msg$i\"}],\"stream\":true}"; done
```

## 测试

```bash
python -m venv .venv
.venv/Scripts/pip install -r router/requirements.txt -r console/backend/requirements.txt pytest
cd router && python -m pytest app/tests -q          # 调度器/粘性单测（7 例）
cd ../console/backend && python -m pytest tests -q  # 控制台冒烟（2 例）
```

## 路线图

P0 脚手架（本仓库）→ P1 纳管+MVP → P2 智能调度+服务管控+多租户 →
P3 高可用+二次开发 → P4 多机并行单模型 → P5 离线交付。
详见 `docs/PROPOSAL.md`（v1.6 定稿）。
