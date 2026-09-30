# 衡枢 HENGSHU · 多机推理 API 负载均衡平台

> 万流归一，衡以分之。

统一 OpenAI 兼容入口（VIP:9000），SSH 纳管异构推理机集群：评分调度 + 会话保持 + 故障迁移，控制台（:8080）全节点可部署，气隙 tar 交付。

## 组件

| 组件 | 说明 | 端口 |
| --- | --- | --- |
| `router/` | OpenAI 兼容入口：评分调度、sticky 会话、N 次重试迁移、X-Type 规则路由、多租户 Key 认证 + QPS 限流 + token 计量 | 9000 |
| `console/` | 管理控制台（Vue3 001 HUD）：驾驶舱、节点管理、Web 终端（审计录屏）、服务启停、租户、规则、审计日志 | 8080 |
| `agent/` | 节点 Agent：心跳 + GPU 采集 + 服务管控（docker/process 双后端，spec 持久化） | 9100 |
| `dist/agent-*.tar` | 气隙交付产物（installer 按目标机 `uname -m` 自动选 x86_64 / aarch64） | — |

## 硬件支持矩阵

| 平台 | 架构 | GPU 采集路径 | 状态 |
| --- | --- | --- | --- |
| x86 + NVIDIA（L4 / RTX 6000 / L40S / H100 PCIe） | amd64 | pynvml（`--gpus all` 标准容器栈） | 单测 + 安装链路覆盖；待真机验收 |
| Jetson（Orin 等） | arm64 | tegrastats（容器内注入）；无 tegrastats 时 pynvml/降级 | 解析单测覆盖；待真机验收 |
| DGX Spark（GB10 统一内存） | arm64 | pynvml；显存 NotSupported 自动降级 `/proc/meminfo` 近似 | **S1 真机已验证**（温度/功耗真实回流） |

推理服务容器下发统一 `--gpus all` + `--restart unless-stopped`，镜像缺失先 `docker pull`（气隙环境跳过自动 pull，支持离线装载镜像）。

## 快速开始（开发）

```bash
bash scripts/dev-up.sh           # console :8080 + router :9000 + fakeredis
cd console/frontend && npm run dev   # 前端热更（可选）
```

登录：admin / `hengshu-dev`（`ADMIN_PASSWORD_HASH` 由 `scripts/gen_admin_hash.py` 生成）。

多用户：「用户管理」页（admin 专属）可建账户。角色：`viewer` 只读 / `operator` 运维（节点·服务·规则·租户变更）/ `admin` 全部（含用户·设置·Web 终端）。用户存 Redis `hs:user:*`，首次启动自动把 env admin 种子入库；禁用/删除即时生效；改密在「系统设置」（写入用户库）。

## 添加节点

控制台「节点管理 → 添加节点」：node_id + IP + SSH 用户/密码（或私钥）→ SSH 推送安装 Agent 容器。
安装器按 `uname -m` 选择 `dist/agent-x86_64.tar` / `dist/agent-aarch64.tar`，TOFU 指纹入库，Redis 地址取 `REDIS_ADVERTISE`（真机跨网段必配）。

构建双架构 tar：`bash scripts/build-agent-tars.sh`（需要 Docker Desktop + buildx）。

## 租户与路由

- 租户管理页创建租户 → 得到 `sk-...` Key；Router 同时认 admin key（`ROUTER_API_KEY`）与租户 Key。
- 租户 QPS 超限额 → 429；token 用量累计在 `hs:tenant:{id}.tokens_total`。
- 规则路由：客户端 `X-Type: code`（或 `body.type`）→ 控制台规则表改写目标模型。

## Webhook

`HENGSHU_WEBHOOK_URL` 环境变量启用：`install.result`（节点安装成败）、`terminal.blocked`（高危命令拦截）。

## 测试

```bash
cd agent && pytest tests            # 15
cd router && pytest app/tests       # 10
cd console/backend && pytest tests  # 12
```

## 尚未实现（P3）

- N 主一备 VIP 漂移（Keepalived）、多机并行 Ray、HA 控制台
