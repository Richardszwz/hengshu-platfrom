# 衡枢 HENGSHU · 多机推理 API 负载均衡器 · 项目方案书与规划

> **产品名：衡枢（Héngshū）** · 负载均衡 + 调度枢纽 · Slogan：万流归一，衡以分之
> 版本 v1.6（定稿）· 2026-09-24 · 状态：**已评审定稿，可进 P0**
> v1.1 变更：新增 Web 控制台（SSH 纳管 + 推送安装 + 监控仪表盘）、主备 LB（keepalived VIP）
> v1.2 变更：控制台无状态化全节点部署 + 管理 VIP :8080（管理节点挂不影响控制台）；凭证主密钥改环境变量派生；Redis 主从+哨兵
> v1.3 变更：管理员登录认证；模型服务远程启停（自定义启动命令）；Web 终端（点击推理机直接命令行）
> v1.4 变更：多机并行启动一个模型（Ray 跨节点分布式推理）；规则路由（客户端 type 字段直达对应模型，默认兜底池）
> v1.5 变更：定位升级为商业化交付产品；技术栈定稿；多租户正式需求；API 二次开发四层能力
> v1.6 变更：**商业化五项决策定稿**——暂不做 License（项目制交付）；按气隙环境设计离线交付；多租户记 token 用量不做账单；二次开发首期只做 REST API+Webhook（Transformer/鉴权适配器入 v1.2  backlog）；首期固定品牌（白标入 v1.2 backlog）。P3 缩减为 2 周

---

## 1. 目标与需求

**一句话目标：** 第一台机器安装本服务后成为**管理节点**，通过 Web 控制台以 SSH 方式纳管其余推理机（IP + 账密），自动采集资源信息形成监控仪表盘，并能向推理机**远程推送安装** Agent/服务；对外提供统一的 OpenAI 兼容 API 入口，按负载调度，且**主备 LB** 防止均衡器单点故障。

| 需求 | 说明 |
| --- | --- |
| SSH 纳管 | 控制台添加节点：IP + 用户名 + 密码（或密钥），后台自动建立 SSH 加密连接，采集机器信息 |
| 监控仪表盘 | 各节点 GPU（利用率/显存/温度/功耗）、CPU、内存、磁盘、网络、模型实例状态统一展示 |
| 远程推送安装 | 添加节点后可一键把 Agent（及备用 LB）远程安装到目标机，自动注册上线 |
| 管理认证 | 控制台强制管理员登录；首次启动创建 admin；密码 argon2 哈希存 Redis；会话 token TTL 8h；登录限流+审计 |
| 模型服务管控 | 在控制台对任意已纳管推理机启动/停止/重启模型服务，**启动命令由管理员自定义**（vllm serve / docker run / systemctl 均可）；stdout/stderr 实时日志回流 |
| Web 终端 | 节点列表点"终端"→ 浏览器内嵌 xterm.js 打开该机器交互式 Shell（经控制台 asyncssh 转发 PTY） |
| 多机并行单模型 | 单模型跨多台推理机分布式启动（Ray 集群 + TP/PP），平台编排 head/worker，注册为**一个逻辑实例** |
| 规则路由 | 客户端请求带 `type`（如 code/vision），均衡器按规则表**直达**对应模型推理机；不带 type 或规则未命中走默认模型池 |
| 模型池化调度 | 多机多模型组成池子，按实时负载分配（负载评分 + 会话亲和） |
| 会话上下文保持 | 同用户会话路由到同实例（KV cache 复用），故障时历史可恢复 |
| 主备 API | 两台 LB 主备热备 + VIP 漂移，主均衡器离线自动切换，对外服务不中断 |

**非目标（本期不做，入 v1.2 backlog）：** License 授权模块、Transformer 改写钩子、自定义鉴权适配器、白标交付、token 硬配额/计费报表、模型自动弹性伸缩、跨节点 KV cache 迁移。

**产品差异化定位（一句话）**：不是又一个 API 代理（LiteLLM/One-API 已免费），而是"**GPU 推理集群的运维平台 + API 入口**"——模型生命周期、SSH 纳管、Web 终端、多机分布式推理、会话保持，这些是纯软件代理给不了的。此句进交付文档引言。

---

## 2. 关键设计决策

### 2.1 会话上下文保持 —— 混合方案

| 方案 | 原理 | 优点 | 代价 | 结论 |
| --- | --- | --- | --- | --- |
| A. 粘性路由 | `session_id` 一致性哈希固定到某实例 | 零额外存储；KV cache / 前缀缓存全命中 | 实例宕机会话中断 | 默认路径 |
| B. 外部会话存储 | 对话历史存 Redis，任意实例取历史重放 | 故障透明、可横向扩展 | 长上下文重放 prefill 延迟高 | 兜底路径 |
| **C. 混合（采用）** | 正常走 A；绑定实例失效时走 B（Redis 拉历史重放再重绑） | 兼顾速度与可用性 | 复杂度略增 | **采用** |

绑定关系存 Redis：`sess:{id} -> {node_id, model, ts}`，TTL 30min。

### 2.2 负载均衡器选型

| 选项 | 负载感知 | 会话保持 | 多框架 | 结论 |
| --- | --- | --- | --- | --- |
| **自研 Router（FastAPI）** | 完全可控（自定义评分） | 原生支持混合方案 | vLLM/TGI/TRT-LLM/Ollama | **采用** |
| LiteLLM Proxy | 弱 | 不支持 | 多 | 仅作对照原型 |
| vLLM 自带 router | 中 | 不支持 | 仅 vLLM | 不适用异构 |
| Nginx/HAProxy | 需二开 | 仅 cookie | 无模型感知 | 不适用 |

### 2.3 技术栈（v1.5 定稿）

| 组件 | 选型 | 理由 |
| --- | --- | --- |
| API Router | **Python 3.11 + FastAPI + httpx** | 异步透传 SSE 原生；团队栈统一；OpenAI 兼容生态最成熟 |
| Web 控制台后端 | **Python 3.11 + FastAPI**（与 Router 同栈，可共容器也可分容器） | 一套代码风格、一套依赖管理 |
| Web 控制台前端 | **Vue 3 + Vite + Element Plus + ECharts** | 交付级 UI 组件库全（表格/表单/终端嵌页）；国内团队生态熟；打包体积小 |
| Web 终端 | **xterm.js** + WebSocket | 终端事实标准；Vue 可无缝嵌 |
| SSH 通道 | **asyncssh** | 纯 Python 异步，支持密码/密钥/PTY/文件传输，无 libssh 依赖商用打包干净 |
| 节点 Agent | **Python 3.11**（与后端同栈，复用采集/上报代码） | 一处改动两端复用 |
| 注册/会话/指标 | **Redis 7**（主从+哨兵） | 已定 |
| 审计/操作日志 | **落盘 JSONL + 每日轮转**（不进 Redis，防膨胀） | 商用合规可导出 |
| 部署形态 | **Docker Compose（单机全栈）** + 离线 tar 包 | 客户机房无外网也能装；compose 一份文件管所有组件 |
| 推理框架 | vLLM / TGI / TRT-LLM / Ollama 镜像**预置本地 registry**，默认全装、可按需启用 | 见 §2.5 |

**为什么选 Python 全栈**：这个项目核心是"异步网络代理 + SSH 编排 + 进程管理"——全是 Python 强项；Agent 与后端同栈意味着采集协议、加解密、数据模型代码直接复用；交付物是 Docker 镜像，客户不关心源码语言，只关心稳定。

**为什么前端选 Vue3 + Element Plus 而非 React**：管理后台是重表单/重表格场景，Element Plus 的 pro 级组件（穿梭框、描述列表、统计卡片）现成度高，开发周期最短；面向中文运维客户的交付产品此栈维护成本最低。

### 2.4 节点纳管与远程部署（SSH bootstrap）★ v1.1 新增

**核心原则：SSH 只用于一次性 bootstrap，运行期走 Agent 反向通道。**

```
添加节点（控制台 UI: IP + 端口 + 用户名 + 密码/密钥）
   │
   ▼ ① 建立连接（asyncssh）
   │   · 首次连接校验主机指纹（TOFU：UI 显示指纹，用户确认后信任）
   │   · 采集机器画像：GPU 型号/数量（nvidia-smi）、OS、Docker、磁盘
   ▼ ② 一键"推送安装"（用户触发）
   │   · 控制台生成节点专属 token（注册凭证，一次性）
   │   · SFTP 上传 agent 包 + docker-compose → 远程执行安装脚本
   │   · Agent 启动后携 token 向控制台反向注册（HTTPS/ws）
   ▼ ③ 运行期
       · 控制台下发的运维指令、指标上报全走 Agent 通道
       · SSH 保留为应急通道（控制台生成密钥对注入 authorized_keys，密码可随后禁用）
```

**凭证安全：**
- 密码/私钥 AES-256-GCM 加密，密文存 Redis；主密钥 = 部署时注入的环境变量（全节点一致），**不依赖机器指纹**——控制台无状态、需在任一节点解密；不进代码库
- 密码仅在首次 bootstrap 使用；支持只启用密钥登录的节点
- 所有 SSH 操作留审计日志（谁、何时、对哪台、执行了什么；落盘 JSONL，每日轮转，保留 90 天可配）

### 2.5 推理框架管理（默认全装 + 可后装）★ v1.5 新增

Q1 确认结论：四种框架**默认全部安装**，同时保留运行时安装/启用能力。

- **预置本地 registry**：交付包内携带 vLLM / TGI / TRT-LLM / Ollama 离线镜像 tar，管理节点导入后形成私有 registry，客户机房无外网也能装
- **节点画像→框架匹配**：纳管采集 GPU 型号/驱动/CUDA 版本后，控制台自动给出"该节点可装框架清单"（如 Jetson 只显示 TRT-LLM/Ollama）
- **按需启用**：框架装而不启，创建模型服务时才拉起对应容器，避免空转占显存
- **后装能力**：新框架版本发布 → 控制台上传新镜像 → 推送至指定节点 → 注册表新增可用框架

---

## 3. 总体架构

```
                 ┌────────────── 接入层（主备） ──────────────┐
   多用户 API ──►│  VIP :9000 (VRRP) ──► Router 主 ──► Router 备(热备)  │
                 └──────┬────────────────────┬──────────────┘
                        │ 查/写绑定、注册表   │ 同一 Redis，状态实时一致
   ┌────────────────────┼────────────────────┼──────────────────────────┐
   │ 管理节点(第一台)    │                    │                          │
   │  Web 控制台 ──SSH 纳管/推送安装────►│  数据面 · 推理集群        │
   │  Redis · Grafana · 凭证库          │   节点A vLLM / 节点B TGI  │
   │                                    │   节点C Jetson / 弹性池D  │
   └────────────────────────────────────┴──────────────────────────┘
```

### 3.1 推理节点 Agent（sidecar）

每台推理机一个 Agent：

1. **采集**：`nvidia-smi`（GPU util、显存、温度、功耗）、框架指标（vLLM/TGI 的 running/waiting 请求数、KV cache 使用率）。
2. **上报**：每 5s 心跳写 Redis `node:{id}`（模型列表、容量、指标），TTL 15s。
3. **健康**：本地探测各模型端口 `/health`，失败即上报 `draining`。

统一上报格式，屏蔽框架差异：

```json
{
  "node_id": "gpu-node-a",
  "models": [{"name": "qwen3-30b", "served": "Qwen3-30B-A3B", "framework": "vllm", "port": 8000}],
  "gpu": {"util": 0.82, "vram_used_mb": 42600, "vram_total_mb": 46068, "temp_c": 71, "power_w": 210},
  "cpu": {"util": 0.34}, "mem": {"used_mb": 52000, "total_mb": 128000},
  "disk": {"used_pct": 0.41}, "net": {"rx_mbps": 88, "tx_mbps": 240},
  "queue": {"running": 12, "waiting": 3},
  "latency": {"ttft_ms_p50": 340, "tps_p50": 68},
  "status": "active"
}
```

### 3.2 监控仪表盘（控制台 Web UI）★ v1.1 新增

- **节点总览卡片**：在线状态、GPU util/显存/温度/功耗、CPU/内存/磁盘、Agent 版本
- **模型视图**：每实例 running/waiting 请求、TTFT、tokens/s、KV cache 占用
- **调度视图**：会话绑定分布、粘性命中率、故障迁移事件流水
- **告警**：节点掉线、GPU 过热（>85°C）、显存打满、队列积压、主备切换事件
- 数据来源：Agent 5s 心跳（粗粒度实时）+ Prometheus 抓取（细粒度历史）

### 3.3 主备 API（keepalived VIP 热备）★ v1.1 新增

**架构：所有节点部署相同 Router 服务，一主多备（N 节点同时只激活一台），节点间 VRRP 心跳互检，主掉线后由备机按优先级链接管。**

| 项 | 设计 |
| --- | --- |
| 拓扑 | 集群内所有机器装 Router（推理节点上为低优先级备实例），keepalived 同 VRRP 实例 |
| VIP | 如 `192.168.66.100:9000`，客户端只配 VIP；只有主节点持有 |
| VIP vs Redis 分工 | **VIP（VRRP）= 对外单一 IP，负责流量入口漂移；Redis = 共享状态（绑定表/注册表/历史），负责会话连续**。两者缺一不可：没 VIP 客户端要知道多台机器；没 Redis 切换后会话绑定清空、对话丢失 |
| 心跳检测 | VRRP advert 1s，连续 3 个未收 → master_down，备机接管 VIP（gratuitous ARP），**中断 3–5s** |
| 接管顺序 | 默认按 VRRP priority 链（A→B→C）；要求平权先到先得则同优先级 + `nopreempt` |
| 热备 | 备机 Router 常运行、连同一 Redis，绑定表实时一致，接管即服务 |
| 会话连续性 | 切换后新主从 Redis 读原绑定继续路由；进行中的 SSE 断开，客户端重试即恢复 |
| 防脑裂 | 双仲裁：VRRP 同组同网段 + Redis 选主锁（谁能写 `lb:master` 谁服务） |
| Redis 落点 | 独立部署（见下），**不随任一 Router 节点**，避免一起失效 |
| Redis 高可用 | **主从+哨兵**（或两台互为主从）：master 挂 → sentinel 30s 内提升 replica；绑定表注册表在 replica 上实时同步，会话连续性不依赖管理节点存活 |
| 控制台部署 | **无状态服务，全节点部署**（状态全存 Redis：凭证密文、节点配置、审计日志、安装任务进度，单一事实源在 Redis）；keepalived 第二个 VIP（如 `:8080`）漂移，管理员固定一个 URL 访问，kill 任一节点控制台不中断。与 API VIP :9000 同 VRRP 实例共漂（也可分实例独立切换） |

> 约束：VRRP 要求主备同二层网段（当前机房/办公网环境满足）。若未来跨网段，降级方案 = DNS 双 A 记录 + 5s TTL，切换 10–30s。

### 3.4 调度算法（每次请求执行）

```
1. 解析模型名 M、session_id S
2. 候选集 = 注册表中 active 且服务模型 M 的节点；为空 → 503
3. bound = GET sess:{S}
4. 若 bound ∈ 候选集 且未过载（waiting < 阈值）：score(bound) *= 0.7   # 粘性加成
5. score = 0.35*gpu_util + 0.25*vram_ratio + 0.25*queue_ratio + 0.15*ttft_norm
6. 选最小 score 节点转发（SSE 流式透传）
7. 成功后 SET sess:{S} TTL 30min；绑定节点失效 → Redis 历史重放重绑（方案 C）
```

### 3.5 健康检查与熔断

- 心跳 15s 无 → `suspect`（不接新会话）；30s → 摘除
- 5xx 比例 > 50%（1min 窗口）→ 熔断 half-open，探针恢复
- 摘除节点的绑定批量失效，下一请求触发历史重放，用户无感

### 3.6 API 设计（客户端完全 OpenAI 兼容）

```bash
curl http://192.168.66.100:9000/v1/chat/completions \
  -H "Authorization: Bearer <key>" \
  -H "X-Session-Id: user-42" \
  -d '{"model": "qwen3-30b", "messages": [...], "stream": true}'
# 响应头：X-Routed-Node / X-Routed-Model（排障）
```

`X-Session-Id` 不传 = 无状态最优节点。管理面：`POST /admin/nodes`（SSH 纳管）、`POST /admin/nodes/{id}/install`（推送安装）、`GET /admin/pool`、`GET /admin/sessions/{id}`、`GET /metrics`。

### 3.7 部署拓扑（对齐现有 PCIe 服务器矩阵）

| 角色 | 建议落点 | 说明 |
| --- | --- | --- |
| 管理节点（第一台） | 常开 x86 小机 | 控制台（无状态全节点部署）+ Grafana + 管理 VIP :8080，4C8G 起，无 GPU 需求 |
| Redis 主 / 备 | 两台任意机器（可与 LB 复用，避免与管理节点同机） | 主从+哨兵，sentinel 3 节点或共用 |
| Router（API 入口） | 所有节点都装（推理节点上为低优先级备实例） | keepalived VRRP 同组，VIP 漂移，同时只激活一台 |
| 推理节点 | L40S / RTX 6000 / H100 PCIe 主力，Jetson Orin 边缘 | Agent + 推理框架各一个容器，SSH 推送安装 |
| 网络 | 主备 LB 需同二层（VRRP）；其余内网互通 | Jetson 弱网节点心跳超时单独调参 |

---

## 3.8 平台管理功能（控制台）★ v1.3 新增

### 3.8.1 管理员登录与权限

```
首次启动 → 强制创建 admin 账号（用户名+密码）
日常登录 → 控制台校验 → 会话 token 写 Redis（TTL 8h）→ 浏览器 Cookie
```

- 密码 argon2id 哈希存 Redis，**永不存明文**
- 登录接口限流（5 次/分钟/IP），失败审计
- 会话随 Redis 共享 → 控制台 VIP 漂移后登录态不丢
- 预留角色位（P4 可加只读 viewer：能看仪表盘/日志，不能操作节点与服务）

### 3.8.2 模型服务远程启停（自定义启动命令）

控制台把"模型服务"定义为一条记录，管理员在 UI 上针对某台已纳管节点填写：

```json
{
  "service": "qwen3-30b",
  "node": "gpu-node-a",
  "start_cmd": "vllm serve /models/Qwen3-30B-A3B --port 8000 --tensor-parallel-size 4 --max-model-len 32768",
  "stop_cmd": "",              // 可空：默认 kill 进程组
  "workdir": "/workspace",
  "env": {"CUDA_VISIBLE_DEVICES": "0,1,2,3"},
  "health_path": "/health"
}
```

**执行链路**：控制台下发指令 → 节点 Agent 以 `subprocess` 执行 `start_cmd`（setsid 独立进程组，不随 Agent 重启丢失）→ stdout/stderr 逐行经 WebSocket 回推控制台"日志"页 → Agent 探测 `health_path` 成功后自动把该模型写入注册表（Router 立即可调度）。

- 支持 启动 / 停止 / 重启 三操作；停止默认 kill 整个进程组
- 服务定义存 Redis，控制台无状态不受影响
- 与推送安装的关系：Agent 管"机器在不在"，服务管控管"模型起没起"，两层正交

### 3.8.3 Web 终端

```
浏览器 xterm.js ──WebSocket──► 控制台实例 ──asyncssh──► 目标推理机 PTY (bash)
```

- 节点列表每行一个"终端"按钮 → 新标签页打开交互 Shell
- 控制台实例用该节点已保存的 SSH 凭证建连（优先密钥，密码兜底）
- 支持交互式程序（htop、nvidia-smi -l、vim）、终端 resize、Ctrl+C 等信号透传
- 全会话审计：谁、何时、连的哪台、时长；命令 transcript 可选落 Redis
- **商用安全加固（v1.6 评审新增）**：会话全程 asciinema 格式录屏落盘（客户安全评审要求）；命令黑白名单可配（默认不限制，白名单模式只放行只读命令）；闲置 30min 自动断开
- 仅 admin 角色可用

### 3.8.4 客户端视角不变

以上全部是**管理面**能力。推理客户端始终只面对 `VIP:9000` + 模型名 + `X-Session-Id`，对接哪台推理机、模型在哪起的、控制台漂到哪——全部无感。

---

## 3.9 高级能力 ★ v1.4 新增

### 3.9.1 多机并行启动一个模型（跨节点分布式推理）

**场景**：单模型超出单机显存（如 235B 稠密/超大 MoE），需把权重切到多台机器联合 serving。

**服务定义扩展**：

```json
{
  "service": "qwen3-235b-a22b",
  "mode": "distributed",
  "nodes": ["gpu-node-a", "gpu-node-b"],
  "head": "gpu-node-a",
  "parallelism": {"tp": 16, "pp": 1},
  "start_cmd": "vllm serve /models/Qwen3-235B --tensor-parallel-size 16 --pipeline-parallel-size 1 --distributed-executor-backend ray --port 8000"
}
```

**平台编排流程**：

```
控制台创建分布式服务（勾选 N 台节点，指定 head）
  → head 节点 Agent: ray start --head --port=6379
  → worker 节点 Agent: ray start --address=<head>:6379
  → head Agent: 执行 start_cmd（vLLM 经 Ray 把 shard 摊到所有节点 GPU）
  → 注册：一个逻辑实例 {model, nodes:[...], entry: head:8000}
```

**对 Router 透明**：分布式实例注册为**单个实例**，入口=head 的 API 端口，会话亲和绑定到实例（head）而非具体某台 worker。负载评分以 head 上报的聚合指标为准（Agent 汇总整个 Ray group 的 GPU/队列）。

**组健康语义**：任一节点挂 → 整个实例不可用（TP 的固有特性）→ 整组从注册表摘除，存量会话走历史重放兜底迁移到其他实例。

**硬约束**：

| 约束 | 说明 |
| --- | --- |
| 网络带宽 | 跨机 TP 要求 ≥10GbE，推荐 25/100GbE 或 IB；弱网环境改用 PP（流水线并行，对带宽容忍度高） |
| 节点同质 | 同组节点 GPU 型号/数量一致，否则 TP 效率坍塌 |
| 框架支持 | 当前按 vLLM+Ray 设计；TGI/TRT-LLM 分布式方案各异，后续按 Agent 抽象层扩展 |

### 3.9.2 规则路由（按类型直达对应模型）

**客户端请求扩展**：

```json
POST /v1/chat/completions
{
  "model": "auto",         // 具体模型名 或 auto
  "type": "code",          // 可选：code / vision / chat / 管理员自定义
  "messages": [...]
}
```

**Router 解析顺序**（每次请求）：

```
1. model 指定具体名      → 按模型路由（v1.0 既有逻辑）
2. type 存在             → 查规则表 route:rule:{type} → 目标模型 → 路由
3. type 缺失或规则未命中 → 默认模型池（池内按负载评分调度）
```

**规则表（控制台 CRUD，存 Redis）**：

| 优先级 | 条件 | 目标模型 | 兜底 |
| --- | --- | --- | --- |
| 10 | type=code | qwen2.5-coder-32b | default 池 |
| 20 | type=vision | qwen2.5-vl-72b | default 池 |
| 90 | model=auto / 无 type | qwen3-30b · llama-3.3 | 503 |

- 目标模型无健康节点 → 按规则配置的兜底走（默认池或明确报错）
- 规则表热更新（Redis 读写），Router 每次请求实时查，无需重启
- `type` 字段对 OpenAI SDK 透传：走 `extra_body` 或直接进 payload，Router 解析后即丢弃，不转发给推理机

---

## 3.10 多租户隔离 ★ v1.5 正式需求（Q4 确认）

**模型**：平台 admin 创建 tenant，每个 tenant 绑定三样资源——

| 绑定 | 说明 |
| --- | --- |
| 节点子池 | 若干推理节点（可与 default 池重叠或独占） |
| 模型子集 | 该租户可调用的模型清单 |
| API Key 集 | 每租户独立 key 集，key → tenant 映射存 Redis |

**路由流程**（在既有调度前加一层过滤）：

```
请求 → 解析 API Key → tenant
     → 候选节点 = 全局池 ∩ tenant 节点子池
     → 模型校验：请求的模型 ∈ tenant 模型子集？否 → 403
     → 进入既有调度评分（粘性→评分→转发）
```

**数据模型**：node / model / apikey 全带 `tenant` 字段（`default` = 平台公共池，admin 自身）。P1 建表即带此字段，P2 实现过滤逻辑。

**配额与限流**：每租户独立 QPS 限流（滑动窗口，Redis 实现）；**token 用量逐请求记录（Redis 计数 + 定时落盘），控制台可查可导出，不做账单与硬配额**（v1.6 决策，私有化部署客户自算计费）。

**控制台**：tenant 管理页（CRUD + 资源绑定 + 用量看板：QPS 曲线、token 消耗、活跃 session 数）。

**工作量**：约 1.5 周（注册表 tenant 化 + Router 过滤 + 限流 + 控制台租户页），并入 P2。

---

## 3.11 API 二次开发能力 ★ v1.5 新增（v1.6 裁剪定稿）

面向开放市场销售，客户需要把 LB 嵌进自己的系统。**首期交付两层，另外两层入 v1.2 backlog**：

### 3.11.1 管理 REST API（首期交付，全套 OpenAPI 3.1 自动生成文档）

平台一切皆可编程控制：节点 CRUD、推送安装、服务启停、tenant 管理、规则表、用量查询、审计导出。

```bash
curl -X POST https://<vip>:8080/api/v1/services \
  -H "X-Admin-Token: <pat>" \
  -d '{"service":"qwen3-30b","node":"gpu-node-a","start_cmd":"vllm serve ..."}'
```

- 认证用 Personal Access Token（admin 可签发、可撤销、可设权限范围）
- OpenAPI 文档随版本发布，客户据此生成自己的 SDK

### 3.11.2 Webhook 事件推送（首期交付）

平台事件实时推客户系统：

| 事件 | 时机 |
| --- | --- |
| `node.up` / `node.down` | 节点心跳状态翻转 |
| `service.started` / `service.failed` | 模型服务健康探测结果 |
| `request.completed` | 每次推理完成（可选，含延迟/token 数） |
| `tenant.quota.warning` | 用量达阈值 |

- 控制台配置回调 URL + 密钥（HMAC-SHA256 签名防伪造）
- 失败重试 3 次（指数退避），死信落盘可查

### 3.11.3 预留扩展位（v1.2 backlog，接口先留好）

- **Transformer 请求/响应改写**：声明式 JSONPath 规则 + Python 脚本沙箱（`def transform(req)`，受限子进程、无网络、100ms 超时）
- **自定义鉴权适配器**：客户上传 `def auth(headers) -> tenant|None` 对接自有 SSO/OAuth

首期代码在 Router 请求链路上预留 hook 挂载点（函数指针注册表），v1.2 实现即插即用，不动主链路。

---

## 4. 项目规划

| 阶段 | 周期 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| **P0 脚手架** | 3 天 | 仓库结构、docker-compose（控制台+Redis+Router）、CI | `docker compose up` 起服务 |
| **P1 纳管+MVP** | 3 周 | Web 控制台（含 admin 登录）；SSH 添加节点（指纹确认）+ 机器画像采集；一键推送安装 Agent；**框架预置安装（vLLM/TGI/TRT-LLM/Ollama 离线 registry）**；Agent 心跳+注册表（**数据模型带 tenant 字段**）；Router 模型路由+粘性+健康摘除；OpenAI 兼容透传（含 SSE）；基础仪表盘 | 未登录访问控制台任意页面跳登录页；admin 登录后添加 3 台节点并推送安装成功；四框架镜像可离线导入；多 session 续聊 100% 命中同节点；kill 节点后流量自动绕开 |
| **P2 智能调度+服务管控+多租户** | 3.5 周 | 负载评分调度；Redis 会话历史+故障重放；熔断器；告警；Grafana 细粒度看板；模型服务启停（自定义 start_cmd + 实时日志）；Web 终端（xterm.js + SSH PTY）；规则路由（type→模型规则表 + 默认池兜底）；**多租户隔离（tenant 池过滤 + key 映射 + QPS 限流 + 租户用量看板）** | 压测出负载不均对比报告；模拟宕机历史不丢、会话自动迁移；控制台填启动命令成功拉起 vLLM 并出现在注册表可调度；Web 终端可交互执行 nvidia-smi 等命令并留审计；带 type=code 的请求 100% 路由到 coder 模型；**tenant A 的 key 请求不到 tenant B 独占的模型，403** |
| **P3 高可用+二次开发** | 2 周 | keepalived 主备 VIP + Redis 选主锁；Redis 主从+哨兵；控制台无状态化+管理 VIP :8080；API Key 鉴权+限流；节点权重/灰度；扩容钩子；凭证密钥化（SSH 密钥注入、密码禁用）；**管理 REST API 全套（OpenAPI 3.1 + PAT 认证）**；**Webhook 事件推送（HMAC 签名+重试）**；压测报告 | kill 主 LB，VIP 3–5s 内漂移、会话保持；kill Redis master，sentinel 30s 内切换且会话可查；kill 控制台所在节点，:8080 漂移后可登录、凭证可解密、审计完整；用 PAT 调管理 API 完成一次服务启停；Webhook 收到 node.down 事件且签名校验通过 |
| **P4 高级能力** | 2 周 | 多机并行单模型（Ray 集群编排 head/worker、组注册/整组摘除、head 聚合指标上报）；规则路由兜底链调优；分布式实例会话重放验证 | 2 台节点 TP=16 拉起 235B 级模型，注册为单实例可被调度、会话可绑定；kill 组内任一节点，整组摘除 + 存量会话自动迁移 |
| **P5 打磨+交付** | 1.5 周 | 审计日志完善、只读 viewer 角色、UI 打磨；**离线安装包制作（registry tar + compose + 安装脚本）**；**交付文档四件套（部署手册/管理员手册/API 文档/二次开发指南）**；客户环境验收清单 | 在无外网机器上从 tar 包完成全流程安装；文档四件套评审通过 |

> **UI 原型（2026-09-24）**：`sketches/` 下 3 个仪表盘设计变体已出并实测验证——`001-hud-cockpit`（HUD 驾驶舱）、`002-aurora-glass`（玻璃拟态商用向）、`003-ops-matrix`（磷光终端矩阵）。方向确认后作为 P1/P2 前端视觉基准。

**主要风险与对策**

| 风险 | 对策 |
| --- | --- |
| SSH 凭证泄露 | AES-GCM 加密 + 主密钥派生；密码仅 bootstrap；审计日志 |
| VRRP 被网络设备/虚拟化屏蔽 | 预先用两台测试机验证；备选 DNS 双 A + 短 TTL |
| 各框架指标口径不一 | Agent 统一抽象层，缺指标维度自动裁剪 |
| 长会话重放成本高 | 粘性路由保证正常路径不触发；超长历史摘要/截断 |
| Redis 成为新单点 | P3 起 Redis 哨兵或主备；会话可容忍短时只读降级 |
| Router 备机脑裂双写 | Redis 选主锁 + VRRP 双仲裁，备机无锁拒绝服务 |
| Web 终端成为入侵捷径 | 仅 admin 可用 + 全会话审计 + 闲置断开；控制台本身不暴露公网 |
| start_cmd 误操作毁机 | 服务启停仅 admin；命令与执行人全量审计；stop 只 kill 指定进程组 |
| 跨机 TP 被弱网拖垮 | 部署前控制台自动测节点间带宽（iperf3 纳入画像采集），不达标强制建议 PP |
| 分布式组单点拖垮全组 | 整组注册/整组摘除语义明确；会话重放兜底已覆盖；关键大模型建议双组冗余 |

---

## 5. 已确认决策与待评审项

### 已确认（v1.5 定稿依据）

| 问题 | 结论 |
| --- | --- |
| Q1 推理框架 | 四框架**默认全装** + 保留运行时后装能力（§2.5 离线 registry） |
| Q2 规模 | 并发 **≤50** → Router/控制台 2C4G、Redis 2GB 足够（§5 原 Q2 表按"小档"取值） |
| Q3 LB 放置 | **方案 B：与推理节点复用**（Router 低优先级备实例跑在推理节点上） |
| Q4 多租户 | **做**，正式需求（§3.10），P2 实现 |
| Q5 与 8088 | **不联动，独立项目** |
| 产品定位 | **商业化交付产品**，面向开放市场销售；新增 API 二次开发能力（§3.11） |

### 商业化新增评审项（v1.6 已全部定稿）

| 决策项 | 结论 | 实现影响 |
| --- | --- | --- |
| License 机制 | **暂不做**，项目制交付，产品化授权后补 | 无授权模块；v1.2 backlog 预留 |
| 客户外网 | **按气隙环境设计**：离线 tar 包是唯一交付形态，框架镜像更新走"导入新 tar" | §2.5 离线 registry 为唯一形态，P5 交付物以 tar 包验收 |
| 多租户深度 | **资源隔离 + QPS 限流 + token 用量记录**（可查询导出），不做账单/硬配额 | 租户用量看板含 token 累计；账单类功能 v1.2 backlog |
| API 二次开发 | **首期只做 REST API + Webhook**（Transformer/鉴权适配器入 v1.2 backlog） | P3 由 2.5 周缩减为 **2 周** |
| 品牌策略 | **首期固定品牌**，白标（自定义 logo/产品名/主题色）入 v1.2 backlog | 前端主题变量先留好接口，不做配置页 |

**v1.2 产品化 backlog**（本期不做的登记）：License 授权模块、Transformer 改写钩子、自定义鉴权适配器、白标交付、token 硬配额、计费报表。

---

## 附录 · 规格参考

**并发 ≤50 场景的最低部署规格**（Q2 确认后定稿）：

| 组件 | 规格 | 说明 |
| --- | --- | --- |
| Router | 2C4G | 50 并发下 httpx 连接池默认配置即够 |
| 控制台 | 2C4G | 与 Router 可同机 |
| Redis | 2GB + AOF | 50 并发 × 平均 4k tokens 历史 ≈ 5MB 状态，余量 400 倍 |
| 节点 Agent | 每推理机 <200MB | 纯采集进程 |
| 管理节点整机 | 4C8G 起 | 控制台+Redis+Grafana 可同机 |

**推理框架指标采集插件工作量参考**（Q1 确认"全装"后的开发顺序）：vLLM（基准）→ TGI（~2天）→ Ollama（~2天）→ TRT-LLM（~3天）。

---

*以上商业化五项回复后，方案定稿进入 P0。*
