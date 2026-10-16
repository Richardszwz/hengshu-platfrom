<template>
  <AppLayout crumb-prefix="纳管" crumb-title="节点管理">
    <template #top-right>
      <button class="live-btn" @click="showAdd = !showAdd">{{ showAdd ? '收起' : '＋ 添加节点' }}</button>
    </template>

    <!-- 添加节点面板（P1 纳管链路：存凭证 → SSH 推送安装 → 心跳上线） -->
    <div v-if="showAdd" class="panel corner add-panel">
      <p class="p-title">SSH 纳管 · 推送安装 Agent</p>
      <div class="form-grid">
        <label>节点 ID<input v-model="form.node_id" placeholder="如 gpu-node-e（英文/数字/短横线）"></label>
        <label>主机地址<input v-model="form.hostname" placeholder="IP 或主机名"></label>
        <label>SSH 端口<input v-model.number="form.ssh_port" placeholder="22"></label>
        <label>SSH 用户<input v-model="form.username" placeholder="如 root / dgx"></label>
        <label>认证方式
          <select v-model="authType">
            <option value="private_key">私钥</option>
            <option value="password">密码</option>
          </select>
        </label>
        <label class="wide">{{ authType === 'password' ? '密码' : '私钥（PEM 全文）' }}
          <textarea v-model="credential" rows="4"
            :placeholder="authType === 'password' ? 'SSH 密码（提交后 AES-GCM 加密落库）' : '-----BEGIN OPENSSH PRIVATE KEY-----'"></textarea>
        </label>
      </div>
      <div class="add-actions">
        <button class="install-btn" :disabled="busy" @click="submit(true)">{{ busy ? '安装中…' : '存储凭证 + 推送安装' }}</button>
        <button class="store-btn" :disabled="busy" @click="submit(false)">仅存储凭证</button>
        <span class="hint">安装步骤实时写入集群事件流；完成后节点在 3 个心跳周期内自动上线</span>
      </div>
      <div v-if="installResult" class="install-result" :class="{ err: installResult.error }">
        <div v-for="(s, i) in installResult.steps || []" :key="i" class="step">
          <span class="mark" :class="s.includes('失败') || s.includes('ERROR') ? 'bad' : ''">{{ s.includes('失败') ? '✗' : '▸' }}</span>{{ s }}
        </div>
        <div v-if="installResult.error" class="err-line">✗ {{ installResult.error }}</div>
        <div v-else-if="installResult.docker" class="ok-line">✓ 已下发（目标机 Docker {{ installResult.docker }} / {{ installResult.arch }}），等待心跳上线</div>
      </div>
    </div>

    <!-- 节点列表 -->
    <div class="panel corner">
      <p class="p-title">已登记节点<span class="count">（{{ nodes.length }}）</span></p>
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>节点 ID</th><th>主机</th><th>架构</th><th>状态</th>
              <th>实例</th><th>最近心跳</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="n in nodes" :key="n.node_id">
            <tr>
              <td class="id-cell">
                <button class="exp-btn" @click="toggleExpand(n.node_id)">{{ expanded.has(n.node_id) ? '▾' : '▸' }}</button>
                {{ n.node_id }}
              </td>
              <td>{{ n.hostname }} <span class="dim" v-if="n.hostname">:{{ n.ssh_port || 22 }}</span></td>
              <td class="mono">{{ n.arch || '—' }}</td>
              <td>
                <span class="st" :class="n.status === 'online' ? 'ok' : (n.status === 'pending' ? 'wait' : 'bad')">
                  <i></i>{{ n.status === 'online' ? '在线' : (n.status === 'pending' ? '待上线' : '离线') }}
                </span>
              </td>
              <td class="mono link" @click="toggleExpand(n.node_id)">{{ n.instances ?? 0 }}</td>
              <td class="dim">{{ fmtAgo(n.last_seen) }}</td>
              <td>
                <button v-if="n.status === 'online'" class="op go" @click="openLaunch(n)">启动服务</button>
                <button class="op" @click="reinstall(n)" :disabled="reinstalling === n.node_id">
                  {{ reinstalling === n.node_id ? '重装中…' : '重装' }}
                </button>
                <button v-if="n.status === 'online'" class="op warn" @click="stopAgent(n)">停止Agent</button>
                <button v-if="n.status === 'online'" class="op" @click="openNet(n)">网络设置</button>
                <button class="op" @click="openConn(n)">改连接</button>
                <button class="op danger" @click="remove(n)">删除</button>
              </td>
            </tr>
            <!-- 展开：网卡 + 该节点的模型服务实例（监视 + 操作） -->
            <tr v-if="expanded.has(n.node_id)" class="svc-expand">
              <td colspan="7">
                <div class="nic-row" v-if="n.nics?.length">
                  <span class="nic-chip" :class="{ down: !nc.up, virt: nc.virtual }" v-for="nc in n.nics" :key="nc.name">
                    {{ nc.name }} · {{ nc.ip || '未配IP' }}<template v-if="nc.speed"> · {{ nc.speed }}Mb</template>
                  </span>
                  <span class="dim" style="font-size:11px">网卡（红色=未启用，灰色=虚拟；配置在「网络设置」）</span>
                </div>
                <div class="nic-row dim" v-else style="font-size:11px">网卡信息采集中（每分钟刷新）…</div>
                <div class="nic-row" v-if="(modelInv[n.node_id] || []).length">
                  <span class="nic-chip" v-for="mdl in modelInv[n.node_id]" :key="mdl">{{ mdl }}</span>
                  <span class="dim" style="font-size:11px">本机模型库（/models）</span>
                </div>
                <table class="svc-sub" v-if="nodeServices(n.node_id).length">
                  <thead><tr><th>服务</th><th>框架</th><th>状态</th><th>GPU</th><th>TTFT</th><th>TOKENS/S</th><th>操作</th></tr></thead>
                  <tbody>
                    <template v-for="s in nodeServices(n.node_id)" :key="s.inst_id">
                    <tr>
                      <td><b>{{ s.model }}</b></td>
                      <td class="mono">{{ s.framework || '—' }}</td>
                      <td><span class="st" :class="s.status === 'running' ? 'ok' : (s.status === 'crashing' ? 'bad' : 'wait')">
                        <i></i>{{ s.status === 'running' ? '运行中' : (s.status === 'crashing' ? '崩溃循环' : '已停止') }}</span></td>
                      <td class="mono">{{ s.metrics?.m_gpu_util ?? '—' }}%</td>
                      <td class="mono">{{ s.metrics?.m_ttft ? Math.round(s.metrics.m_ttft) + 'ms' : '—' }}</td>
                      <td class="mono">{{ s.metrics?.m_tps ? Math.round(s.metrics.m_tps) : '—' }}</td>
                      <td>
                        <button class="op" @click="svcToggleLogs(s)">{{ svcLogs[s.inst_id] ? '收起日志' : '日志' }}</button>
                        <button v-if="s.status === 'running'" class="op warn" :disabled="svcBusy === s.inst_id" @click="svcStop(s)">停止</button>
                        <button v-else class="op go" :disabled="svcBusy === s.inst_id" @click="svcStart(s)">启动</button>
                        <button class="op danger" :disabled="svcBusy === s.inst_id" @click="svcDel(s)">删除</button>
                      </td>
                    </tr>
                    <tr v-if="svcLogs[s.inst_id]">
                      <td colspan="7"><pre class="svc-log">{{ svcLogs[s.inst_id].join('\n') }}</pre></td>
                    </tr>
                    </template>
                  </tbody>
                </table>
                <div v-else class="dim" style="padding:10px 4px">该节点暂无模型服务 — 点「启动服务」下发一个</div>
              </td>
            </tr>
            </template>
            <tr v-if="!nodes.length">
              <td colspan="7" class="empty">暂无节点 — 点击右上角「添加节点」开始纳管</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="svc-dim">提示：删除仅清除登记与凭证；目标机上的 Agent 容器需 SSH 手动 docker rm -f hengshu-agent</p>
    </div>

    <!-- 多机网络 & NCCL 测试（RoCE） -->
    <div class="panel corner" style="margin-top:12px">
      <p class="p-title">多机网络 &amp; NCCL 测试<span class="count">（RoCE 链路 · 多机模型部署前置）</span></p>
      <div class="nccl-form">
        <label v-for="n in nodes.filter(x => x.status === 'online')" :key="n.node_id" class="nccl-node">
          <input type="checkbox" :checked="ncclSel.has(n.node_id)" @change="toggleNccl(n.node_id)" />
          {{ n.node_id }}<span class="dim"> {{ n.hostname }}</span>
          <select v-if="ncclSel.has(n.node_id)" v-model="ncclNics[n.node_id]" class="inp nccl-nic-sm">
            <option value="" disabled>选网卡</option>
            <option v-for="nc in (n.nics || []).filter((x: any) => x.up && x.ip)" :key="nc.name" :value="nc.name">
              {{ nc.name }} · {{ nc.ip }}</option>
          </select>
          <button v-if="ncclSel.has(n.node_id)" class="op" :disabled="ncclEnvBusy === n.node_id"
                  @click="deployNcclEnv(n.node_id)">
            {{ ncclEnvBusy === n.node_id ? '部署中…' : 'NCCL环境' }}</button>
        </label>
      </div>
      <div class="nccl-form">
        <button class="op" :disabled="!ncclReady || pingRunning" @click="runPing">
          {{ pingRunning ? 'Ping 测试中…' : '① 连通性测试（Ping）' }}</button>
        <button class="op" :disabled="ncclSel.size < 2 || meshRunning" @click="runMesh">
          {{ meshRunning ? '互通配置中…' : '② 节点免密互通' }}</button>
        <button class="op go" :disabled="ncclRunning || !ncclReady" @click="runNccl">
          {{ ncclRunning ? 'NCCL 测试执行中…' : '③ NCCL 测试' }}</button>
      </div>
      <div class="nccl-form">
        <input v-model.trim="ncclCmd" class="inp" style="flex:1;min-width:320px"
               placeholder="自定义测试指令（留空=默认 torchrun all_reduce；可用 $RANK/$WORLD_SIZE/$MASTER_ADDR 环境变量）" />
        <input v-model.trim="ncclEnvText" class="inp" style="flex:1;min-width:280px"
               placeholder="附加环境变量，空格分隔：NCCL_IB_HCA=mlx5_0 NCCL_IB_GID_INDEX=3" />
      </div>
      <div v-if="pingResult" class="nccl-result" :class="{ bad: !pingResult.ok }">
        <div class="nccl-head">{{ pingResult.ok ? '✓ 链路互通' : '✗ 存在不通链路' }}</div>
        <div v-for="(p, i) in pingResult.pairs" :key="i" class="nccl-node-result">
          <span class="st" :class="p.ok && !p.loss_pct ? 'ok' : 'bad'"><i></i>{{ p.ok && !p.loss_pct ? '通' : '不通' }}</span>
          <b>{{ p.dir }}</b>
          <span class="dim">{{ p.ok ? `loss=${p.loss_pct}% rtt=${p.rtt_ms ?? '—'}ms` : (p.error || '') }}</span>
        </div>
      </div>
      <div v-if="meshResult" class="nccl-result" :class="{ bad: !meshResult.ok }">
        <div class="nccl-head">{{ meshResult.ok ? '✓ 免密互通完成：' + meshResult.meshed.join(' ↔ ') : '✗ 部分失败' }}</div>
        <div v-for="(err, nid) in meshResult.errors" :key="nid" class="dim">{{ nid }}: {{ err }}</div>
      </div>
      <div v-if="ncclResult" class="nccl-result" :class="{ bad: !ncclResult.ok }">
        <div class="nccl-head">
          {{ ncclResult.ok ? '✓' : '✗' }} master {{ ncclResult.master }}({{ ncclResult.master_ip }})
          <b v-if="ncclResult.bandwidth" style="color:var(--cyan)">{{ ncclResult.bandwidth }}</b>
        </div>
        <div v-for="r in ncclResult.results" :key="r.node" class="nccl-node-result">
          <span class="st" :class="r.ok ? 'ok' : 'bad'"><i></i>{{ r.ok ? '通过' : '失败' }}</span>
          <b>{{ r.node }}</b> <span class="dim">{{ r.nic }} · {{ r.ip }}</span>
          <pre v-if="!r.ok" class="svc-log">{{ (r.tail || []).join('\n') }}</pre>
        </div>
      </div>
      <p class="svc-dim">流程：选节点选网卡 → ① ping 验证所选网卡互通 → ② 免密互通（节点间 ssh 免密）→ ③ NCCL all_reduce（数据面 RoCE，NCCL_IB_* 在环境变量框配）。NCCL环境按钮=镜像就位+torch/nccl 自检。</p>
    </div>

    <!-- 网络设置对话框（节点行触发） -->
    <div class="mask" :class="{ on: netOpen }" @click="netOpen = false"></div>
    <div class="launch-dialog panel wide" :class="{ open: netOpen }">
      <div class="p-title">网络设置 <span class="sub">{{ netNode?.node_id }} · 网卡配置（RoCE 链路）</span></div>
      <div class="launch-body">
        <table class="svc-sub" v-if="netNics.length">
          <thead><tr><th>网卡</th><th>IP</th><th>速率</th><th>状态</th><th>配置</th></tr></thead>
          <tbody>
            <tr v-for="nc in netNics" :key="nc.name">
              <td class="mono"><b>{{ nc.name }}</b></td>
              <td class="mono">{{ nc.ip || '—' }}</td>
              <td class="mono">{{ nc.speed ? nc.speed + 'Mb' : '—' }}</td>
              <td><span class="st" :class="nc.up ? 'ok' : 'bad'"><i></i>{{ nc.up ? 'UP' : 'DOWN' }}</span></td>
              <td>
                <input v-model.trim="nicIps[nc.name]" class="inp nic-ip-inp" placeholder="IP/前缀，如 10.0.0.5/24" />
                <button class="op go" :disabled="nicBusy" @click="applyNic(nc.name, true)">设IP并启用</button>
                <button class="op" :disabled="nicBusy" @click="applyNic(nc.name, false, true)">仅设IP</button>
                <button class="op" :disabled="nicBusy" @click="toggleNic(nc)">{{ nc.up ? '停用' : '启用' }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="dim">网卡信息加载中…</div>
        <p v-if="netMsg" class="err" :style="netMsg.startsWith('✓') ? 'color:var(--green)' : ''">{{ netMsg }}</p>
      </div>
      <div class="launch-foot">
        <button class="mini-btn" @click="refreshNet">刷新</button>
        <button class="mini-btn" @click="netOpen = false">关闭</button>
      </div>
    </div>

    <!-- 启动服务对话框（节点行触发，节点预选） -->
    <div class="mask" :class="{ on: launchOpen }" @click="launchOpen = false"></div>
    <div class="launch-dialog panel" :class="{ open: launchOpen }">
      <div class="p-title">启动新服务 <span class="sub">下发至 {{ launch.node }} · Docker 运行</span></div>
      <div class="launch-body">
        <label>服务/模型名 <input v-model.trim="launch.name" class="inp" placeholder="如 Qwen3-8B" /></label>
        <label>镜像 <input v-model.trim="launch.image" class="inp" placeholder="docker.1ms.run/vllm/vllm-openai:latest（docker.io 不通就走镜像站前缀；arm64 节点填 GB10/Jetson 兼容镜像）" /></label>
        <label>启动命令 <input v-model.trim="launch.start_cmd" class="inp mono-inp"
          placeholder="vllm serve /models/qwen --port {port} --served-model-name Qwen3-8B" /></label>
        <label>服务端口 <input v-model.number="launch.port" class="inp" type="number" placeholder="8100" /></label>
        <label>环境变量（每行 KEY=VALUE）<textarea v-model="launch.envText" class="inp env-inp" rows="3"
          placeholder="HF_ENDPOINT=https://hf-mirror.com"></textarea></label>
        <label>卷挂载（每行 宿主机路径:容器路径[:ro]，模型权重必配）<textarea v-model="launch.volText" class="inp env-inp" rows="2"
          placeholder="/data/models:/models:ro"></textarea></label>
        <p v-if="launchError" class="err">{{ launchError }}</p>
      </div>
      <div class="launch-foot">
        <button class="mini-btn" @click="launchOpen = false">取消</button>
        <button class="mini-btn go" :disabled="launchBusy" @click="submitLaunch">{{ launchBusy ? '下发中…' : '启动' }}</button>
      </div>
    </div>
    <!-- 改连接对话框 -->
    <div class="mask" :class="{ on: connOpen }" @click="connOpen = false"></div>
    <div class="launch-dialog panel" :class="{ open: connOpen }">
      <div class="p-title">修改连接 <span class="sub">{{ conn.node_id }} · SSH</span></div>
      <div class="launch-body">
        <label>主机地址（IP/主机名）<input v-model.trim="conn.hostname" class="inp" /></label>
        <label>SSH 端口 <input v-model.number="conn.ssh_port" class="inp" type="number" /></label>
        <label>SSH 用户 <input v-model.trim="conn.username" class="inp" /></label>
        <label>新密码 / 私钥（留空 = 不换凭证）
          <textarea v-model="conn.secret" class="inp env-inp" rows="2" placeholder="留空保持不变"></textarea></label>
        <p v-if="connError" class="err">{{ connError }}</p>
      </div>
      <div class="launch-foot">
        <button class="mini-btn" @click="connOpen = false">取消</button>
        <button class="mini-btn go" :disabled="connBusy" @click="submitConn">{{ connBusy ? '保存中…' : '保存' }}</button>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref, watch, computed } from 'vue'
import { api } from '../api/client'
import AppLayout from '../components/AppLayout.vue'

const nodes = ref<any[]>([])
const services = ref<any[]>([])            // 全量服务实例（按节点分组展示在展开行）
const modelInv = ref<Record<string, string[]>>({})  // 节点 → 本机模型目录（/models）
const expanded = ref<Set<string>>(new Set())  // 展开的节点
const svcLogs = ref<Record<string, string[]>>({})  // inst_id → 日志行
const svcBusy = ref('')
const showAdd = ref(false)
const busy = ref(false)
const installResult = ref<any>(null)
const authType = ref<'private_key' | 'password'>('private_key')
const credential = ref('')
let timer = 0

const form = ref({ node_id: '', hostname: '', ssh_port: 22, username: 'root' })

function fmtAgo(ts?: number) {
  if (!ts) return '—'
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts)
  if (s < 5) return '刚刚'
  if (s < 60) return `${s}s 前`
  return `${Math.floor(s / 60)}m${s % 60}s 前`
}

async function refresh() {
  try {
    const d = await api.nodes()
    nodes.value = d.nodes || []
    const sd = await api.services()
    services.value = sd.services || []
    // 模型清单走 Agent 实扫较慢，不阻塞节点刷新
    api.modelsInventory().then((mi: any) => { modelInv.value = mi.inventory || {} }).catch(() => {})
  } catch (e) { console.error(e) }
}

/* ---------- 节点展开：模型服务监视+操作 ---------- */
function nodeServices(nid: string) {
  return services.value.filter((s: any) => s.node === nid)
}
function toggleExpand(nid: string) {
  const s = new Set(expanded.value)
  if (s.has(nid)) s.delete(nid); else s.add(nid)
  expanded.value = s
}
async function svcStop(s: any) {
  svcBusy.value = s.inst_id
  try { await api.stopService(s.node, s.model) } finally { svcBusy.value = '' }
  refresh()
}
async function svcStart(s: any) {
  svcBusy.value = s.inst_id
  try { await api.startService(s.node, s.model) } finally { svcBusy.value = '' }
  setTimeout(refresh, 3000)  // 受理制：容器异步拉起
}
async function svcDel(s: any) {
  if (!confirm(`删除服务 ${s.model} @ ${s.node}？`)) return
  svcBusy.value = s.inst_id
  try { await api.deleteService(s.node, s.model) } finally { svcBusy.value = '' }
  refresh()
}
async function svcToggleLogs(s: any) {
  if (svcLogs.value[s.inst_id]) {
    const m = { ...svcLogs.value }; delete m[s.inst_id]; svcLogs.value = m
    return
  }
  const d = await api.serviceLogs(s.node, s.model, 60)
  svcLogs.value = { ...svcLogs.value, [s.inst_id]: d.lines || [] }
}

function payload() {
  return {
    node_id: form.value.node_id,
    hostname: form.value.hostname,
    ssh_port: form.value.ssh_port || 22,
    username: form.value.username,
    password: authType.value === 'password' ? credential.value : undefined,
    private_key: authType.value === 'private_key' ? credential.value : undefined
  }
}

async function submit(install: boolean) {
  if (!form.value.node_id || !form.value.hostname || !credential.value) {
    installResult.value = { error: '节点 ID / 主机 / 凭证均为必填', steps: [] }
    return
  }
  busy.value = true
  installResult.value = null
  try {
    await api.addNode(payload())
    if (install) {
      installResult.value = await api.installNode(form.value.node_id, payload())
    } else {
      installResult.value = { steps: ['凭证已加密存储'], ok: true }
    }
    refresh()
  } catch (e: any) {
    installResult.value = { error: e?.response?.data?.detail || String(e), steps: [] }
  } finally {
    busy.value = false
  }
}

async function remove(n: any) {
  if (!confirm(`删除节点 ${n.node_id}？${n.status === 'online' ? '在线节点将收到自杀墓碑，Agent 自停后不再出现' : ''}`)) return
  try {
    const d = await api.removeNode(n.node_id)
    if (d.agent_notified) alert(d.msg)
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || '删除失败'
    alert(msg)
  }
  refresh()
}

async function stopAgent(n: any) {
  if (!confirm(`远程停止 ${n.node_id} 的 Agent？\n容器将被删除（防自动重启），节点转离线后才能删除登记。`)) return
  try {
    const d = await api.stopAgent(n.node_id)
    alert(`已下发停止指令：${d.note || d.container || 'OK'}\n约 15 秒后节点转离线。`)
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '停止失败')
  }
  refresh()
}

/* ---------- 改连接对话框 ---------- */
const connOpen = ref(false)
const connBusy = ref(false)
const connError = ref('')
const conn = reactive({ node_id: '', hostname: '', ssh_port: 22, username: '', secret: '' })

function openConn(n: any) {
  conn.node_id = n.node_id
  conn.hostname = n.ssh_host || n.hostname || ''
  conn.ssh_port = n.ssh_port || 22
  conn.username = n.ssh_user || ''
  conn.secret = ''
  connError.value = ''
  connOpen.value = true
}

async function submitConn() {
  connError.value = ''
  if (!conn.hostname || !conn.username) { connError.value = '主机地址/SSH 用户必填'; return }
  connBusy.value = true
  try {
    const payload: any = { hostname: conn.hostname, ssh_port: conn.ssh_port, username: conn.username }
    const sec = conn.secret.trim()
    if (sec) {
      if (sec.includes('PRIVATE KEY')) payload.private_key = sec
      else payload.password = sec
    }
    await api.updateConn(conn.node_id, payload)
    connOpen.value = false
    refresh()
  } catch (e: any) {
    connError.value = e?.response?.data?.detail || '保存失败'
  } finally { connBusy.value = false }
}

/* ---------- 重装（复用已存连接信息+凭证，后端回退） ---------- */
const reinstalling = ref('')

async function reinstall(n: any) {
  if (!confirm(`重装 ${n.node_id}？将停掉旧 Agent 容器并用 dist/ 最新镜像重建（复用已存凭证，无需重填密码）`)) return
  reinstalling.value = n.node_id
  try {
    const d = await api.installNode(n.node_id, { node_id: n.node_id })
    if (d.status === 'ERR' || d.error) {
      alert(`重装失败：${d.msg || d.error}`)
    } else {
      alert('重装已下发，等待节点心跳回来（约 30 秒）')
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '重装失败')
  } finally {
    reinstalling.value = ''
  }
  refresh()
}

/* ---------- 启动服务对话框（入口在节点行） ---------- */
const launchOpen = ref(false)
const launchBusy = ref(false)
const launchError = ref('')
const launch = reactive({ node: '', name: '', image: 'docker.1ms.run/vllm/vllm-openai:latest', start_cmd: '', port: 8100, envText: '', volText: '' })
const nodeSpecs = ref<any[]>([])  // 当前节点历史启动模板

/* ---------- 网络设置对话框（节点行触发） ---------- */
const netOpen = ref(false)
const netNode = ref<any>(null)
const netNics = ref<any[]>([])
const nicIps = ref<Record<string, string>>({})
const nicBusy = ref(false)
const netMsg = ref('')

function openNet(n: any) {
  netNode.value = n
  netNics.value = n.nics || []
  nicIps.value = Object.fromEntries((n.nics || []).map((x: any) => [x.name, x.ip ? x.ip + '/24' : '']))
  netMsg.value = ''
  netOpen.value = true
}

async function refreshNet() {
  await refresh()
  const n = nodes.value.find((x: any) => x.node_id === netNode.value?.node_id)
  if (n) netNics.value = n.nics || []
}

async function applyNic(name: string, bringUp: boolean, ipOnly = false) {
  const ip = nicIps.value[name]
  if (!ip) { netMsg.value = `✗ ${name} 请先填 IP/前缀`; return }
  nicBusy.value = true
  netMsg.value = ''
  try {
    const d = await api.setNic(netNode.value.node_id,
      { name, ip_cidr: ip, up: ipOnly ? null : (bringUp || null) })
    if (d.ok) {
      netNics.value = d.nics
      netMsg.value = `✓ ${name} 已配置 ${ip}`
      refresh()  // 心跳里的网卡清单下一轮刷新
    } else {
      netMsg.value = `✗ ${name} 配置失败：${d.error}`
    }
  } catch (e: any) {
    netMsg.value = `✗ ${e?.response?.data?.detail || e}`
  } finally {
    nicBusy.value = false
  }
}

async function toggleNic(nc: any) {
  nicBusy.value = true
  netMsg.value = ''
  try {
    const d = await api.setNic(netNode.value.node_id, { name: nc.name, up: !nc.up })
    if (d.ok) { netNics.value = d.nics; netMsg.value = `✓ ${nc.name} 已${nc.up ? '停用' : '启用'}`; refresh() }
    else netMsg.value = `✗ ${d.error}`
  } catch (e: any) {
    netMsg.value = `✗ ${e?.response?.data?.detail || e}`
  } finally {
    nicBusy.value = false
  }
}

/* ---------- NCCL 多机网络测试 ---------- */
const ncclSel = ref<Set<string>>(new Set())
const ncclNics = ref<Record<string, string>>({})  // 节点 → 网卡名（异名双网口各自选）
const ncclRunning = ref(false)
const ncclResult = ref<any>(null)

const ncclReady = computed(() =>
  ncclSel.value.size >= 2 && [...ncclSel.value].every(nid => ncclNics.value[nid]))

const pingRunning = ref(false)
const pingResult = ref<any>(null)
const meshRunning = ref(false)
const meshResult = ref<any>(null)
const ncclCmd = ref('')
const ncclEnvText = ref('')
const ncclEnvBusy = ref('')

async function runPing() {
  pingRunning.value = true
  pingResult.value = null
  try {
    pingResult.value = await api.netTest({ nodes: [...ncclSel.value], nics: ncclNics.value })
  } catch (e: any) {
    pingResult.value = { ok: false, pairs: [], error: e?.response?.data?.detail || String(e) }
  } finally {
    pingRunning.value = false
  }
}

async function runMesh() {
  meshRunning.value = true
  meshResult.value = null
  try {
    meshResult.value = await api.sshMesh({ nodes: [...ncclSel.value] })
  } catch (e: any) {
    meshResult.value = { ok: false, errors: { '': e?.response?.data?.detail || String(e) } }
  } finally {
    meshRunning.value = false
  }
}

async function deployNcclEnv(nid: string) {
  ncclEnvBusy.value = nid
  try {
    const d = await api.ncclEnv(nid)
    alert(d.ok ? `✓ ${nid} NCCL 环境就绪：${d.detail}` : `✗ ${nid} 部署失败：${d.error}`)
  } catch (e: any) {
    alert(`✗ ${nid} 部署失败：${e?.response?.data?.detail || e}`)
  } finally {
    ncclEnvBusy.value = ''
  }
}

function toggleNccl(nid: string) {
  const s = new Set(ncclSel.value)
  if (s.has(nid)) {
    s.delete(nid)
    const m = { ...ncclNics.value }; delete m[nid]; ncclNics.value = m
  } else {
    s.add(nid)
    // 默认选该节点第一张 up 网卡
    const n = nodes.value.find((x: any) => x.node_id === nid)
    const firstUp = (n?.nics || []).find((x: any) => x.up)
    if (firstUp && !ncclNics.value[nid]) ncclNics.value = { ...ncclNics.value, [nid]: firstUp.name }
  }
  ncclSel.value = s
}

async function runNccl() {
  ncclRunning.value = true
  ncclResult.value = null
  const env: Record<string, string> = {}
  for (const kv of ncclEnvText.value.split(/\s+/).filter(Boolean)) {
    const i = kv.indexOf('=')
    if (i > 0) env[kv.slice(0, i)] = kv.slice(i + 1)
  }
  try {
    ncclResult.value = await api.ncclTest({
      nodes: [...ncclSel.value], nics: ncclNics.value, cmd: ncclCmd.value, env })
  } catch (e: any) {
    ncclResult.value = { ok: false, results: [],
                         bandwidth: '', error: e?.response?.data?.detail || String(e) }
  } finally {
    ncclRunning.value = false
  }
}

function openLaunch(n: any) {
  launch.node = n.node_id
  launch.volText = n.models_mount || launch.volText  // 预填该节点历史模型卷挂载
  launchError.value = ''
  launchOpen.value = true
  // 拉取该节点历史启动模板（同名模型自动回填全部参数，调好的参数不再丢）
  api.serviceSpecs(n.node_id).then((d: any) => { nodeSpecs.value = d.specs || [] }).catch(() => {})
}

watch(() => launch.name, (name: string) => {
  const sp = nodeSpecs.value.find((s: any) => s.name === name)
  if (!sp) return
  launch.image = sp.image || launch.image
  launch.start_cmd = sp.start_cmd || ''
  launch.port = sp.port || 8100
  launch.envText = Object.entries(sp.env || {}).map(([k, v]) => `${k}=${v}`).join('\n')
  launch.volText = (sp.volumes || []).join('\n')
})

async function submitLaunch() {
  launchError.value = ''
  const missing: string[] = []
  if (!launch.node) missing.push('目标节点')
  if (!launch.name) missing.push('服务/模型名')
  if (!launch.start_cmd) missing.push('启动命令')
  if (!launch.port) missing.push('服务端口')
  if (missing.length) {
    launchError.value = '请填写：' + missing.join('、')
    return
  }
  const env: Record<string, string> = {}
  for (const line of launch.envText.split('\n')) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$/)
    if (m) env[m[1]] = m[2]
  }
  const volumes = launch.volText.split('\n').map(s => s.trim()).filter(s => /^\/[^:]+:\/[^:]+(:ro|:rw)?$/.test(s))
    launchBusy.value = true
  try {
    await api.launchService(launch.node, {
      name: launch.name, image: launch.image,
      start_cmd: launch.start_cmd.replaceAll('{port}', String(launch.port)),
      port: launch.port, workdir: '/workspace', env, volumes
    })
    launchOpen.value = false
    // 不弹窗：进度走驾驶舱「集群事件流」（受理/拉镜像/成败都有事件）
    refresh()
  } catch (e: any) {
    launchError.value = e?.response?.data?.detail || e?.message || '下发失败'
  } finally {
    launchBusy.value = false
  }
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 4000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
/* 节点展开：服务子表 */
.exp-btn { background: none; border: 1px solid var(--line); color: var(--cyan); width: 18px; height: 18px;
  line-height: 1; cursor: pointer; margin-right: 6px; font-size: 10px; padding: 0; }
.link { cursor: pointer; color: var(--cyan); }
.svc-expand > td { background: rgba(0, 229, 255, .03); padding: 10px 14px; }
.svc-sub { width: 100%; border-collapse: collapse; }
.svc-sub th { text-align: left; font-size: 10px; letter-spacing: 1px; color: var(--txt-dim);
  border-bottom: 1px solid var(--line); padding: 4px 8px; }
.svc-sub td { padding: 6px 8px; border-bottom: 1px dashed rgba(0, 229, 255, .08); font-size: 12px; }
.svc-log { margin: 4px 0; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  background: rgba(0, 0, 0, .3); border: 1px solid var(--line); padding: 8px 10px;
  font-size: 11px; line-height: 1.5; color: var(--txt-dim); }
/* 网卡 chips + NCCL 测试 */
.nic-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }
.nic-chip { border: 1px solid rgba(0,229,255,.35); color: var(--cyan); font-size: 11px;
  padding: 2px 8px; font-family: var(--mono); }
.nic-chip.down { border-color: rgba(255,77,106,.4); color: var(--red); opacity: .7; }
.nic-chip.virt { opacity: .45; }
.nccl-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 8px 0; }
.nccl-node { display: flex; gap: 6px; align-items: center; font-size: 12px; cursor: pointer; }
.nccl-nic { width: 260px; }
.nccl-nic-sm { width: 190px; margin-left: 6px; }
.nic-ip-inp { width: 150px; margin-right: 6px; }
.nccl-result { margin-top: 8px; border: 1px solid rgba(45,255,168,.3); padding: 10px 12px; font-size: 12px; }
.nccl-result.bad { border-color: rgba(255,77,106,.4); }
.nccl-head { margin-bottom: 6px; }
.nccl-node-result { margin: 6px 0; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.nccl-node-result .svc-log { width: 100%; }
.add-panel { margin-bottom: 12px; }
.form-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px 14px;
  font-size: 12px; color: var(--txt-dim);
}
.form-grid label { display: flex; flex-direction: column; gap: 4px; }
.form-grid .wide { grid-column: 1 / -1; }
.form-grid input, .form-grid select, .form-grid textarea {
  background: #08131e; border: 1px solid var(--line); color: var(--txt);
  padding: 7px 9px; font-size: 12px; outline: none; font-family: inherit;
}
.form-grid input:focus, .form-grid textarea:focus, .form-grid select:focus { border-color: var(--cyan); }
.form-grid textarea { resize: vertical; font-family: var(--mono); font-size: 11px; }
.add-actions { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.install-btn {
  background: linear-gradient(90deg, var(--cyan), #2a86b8); color: #04121e; border: 0;
  padding: 8px 18px; font-weight: 700; cursor: pointer; font-size: 12px;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.install-btn:disabled { opacity: .5; cursor: wait; }
.store-btn {
  background: transparent; color: var(--txt-dim); border: 1px solid var(--line);
  padding: 7px 14px; cursor: pointer; font-size: 12px;
}
.store-btn:hover { color: var(--cyan); border-color: var(--cyan-dim); }
.hint { color: var(--txt-faint); font-size: 11px; }
.install-result { margin-top: 12px; border: 1px solid var(--line); background: #060f18; padding: 10px; max-height: 220px; overflow: auto; }
.install-result .step { font-size: 11px; padding: 2px 0; font-family: var(--mono); color: var(--txt-dim); }
.install-result .step .mark { color: var(--cyan); margin-right: 6px; }
.install-result .step .mark.bad { color: var(--red); }
.err-line { color: var(--red); font-size: 12px; margin-top: 6px; }
.ok-line { color: var(--green); font-size: 12px; margin-top: 6px; }
.count { color: var(--txt-dim); font-weight: 400; margin-left: 6px; }
.tbl-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th {
  text-align: left; color: var(--txt-dim); font-weight: 600; font-size: 11px;
  padding: 7px 10px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: 8px 10px; border-bottom: 1px solid rgba(20, 40, 60, .4); white-space: nowrap; }
tr:hover td { background: rgba(0, 200, 255, .04); }
.id-cell { color: var(--cyan); font-family: var(--mono); }
.mono { font-family: var(--mono); }
.dim { color: var(--txt-faint); }
.empty { text-align: center; color: var(--txt-faint); padding: 26px !important; }
.st { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
.st i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.st.ok { color: var(--green); } .st.ok i { background: var(--green); box-shadow: 0 0 6px var(--green); }
.st.bad { color: var(--red); } .st.bad i { background: var(--red); }
.st.wait { color: var(--yellow); } .st.wait i { background: var(--yellow); animation: blink 1.6s infinite; }
.op {
  background: transparent; border: 1px solid var(--line); color: var(--txt-dim);
  font-size: 11px; padding: 3px 10px; cursor: pointer; margin-right: 6px;
}
.op:hover { color: var(--red); border-color: rgba(255, 77, 90, .4); }
.op.go { color: var(--cyan, #37b6ff); border-color: rgba(55, 182, 255, .4); }
.op.go:hover { color: #fff; border-color: var(--cyan, #37b6ff); }
.op.warn { color: var(--yellow, #ffc857); border-color: rgba(255, 200, 87, .4); }
.op.warn:hover { color: #fff; border-color: var(--yellow, #ffc857); }
.svc-dim { color: var(--txt-faint); font-size: 11px; margin-top: 8px; }

/* ---------- 启动服务对话框 ---------- */
.mask {
  position: fixed; inset: 0; background: rgba(2, 6, 12, .6); z-index: 90;
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
.mask.on { opacity: 1; pointer-events: auto; }
.launch-dialog {
  position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%) scale(.96);
  width: 520px; z-index: 100; opacity: 0; pointer-events: none;
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  transition: opacity .2s, transform .2s;
}
.launch-dialog.wide { width: 860px; max-width: 92vw; }
.launch-dialog.open { opacity: 1; pointer-events: auto; transform: translate(-50%, -50%) scale(1); }
.launch-dialog .p-title { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 13px; }
.launch-dialog .p-title .sub { color: var(--muted-foreground, #8b98b8); font-size: 11px; margin-left: 8px; font-weight: 400; }
.launch-body { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.launch-body label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--muted-foreground, #8b98b8); }
.launch-body .inp {
  background: rgba(0, 30, 50, .6); border: 1px solid var(--border); color: inherit;
  padding: 7px 10px; font-size: 12px; outline: none; font-family: inherit; border-radius: 4px;
}
.launch-body .inp:focus { border-color: var(--accent, #37b6ff); }
.mono-inp { font-family: var(--font-mono, monospace) !important; }
.env-inp { resize: vertical; }
.launch-body .err { color: #ff5f6b; font-size: 12px; margin: 0; }
.launch-foot {
  display: flex; justify-content: flex-end; gap: 10px; padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.mini-btn {
  background: transparent; border: 1px solid var(--border); color: inherit;
  font-size: 12px; padding: 6px 16px; cursor: pointer; border-radius: 4px;
}
.mini-btn.go { border-color: var(--accent, #37b6ff); color: var(--accent, #37b6ff); }
.mini-btn.go:disabled { opacity: .5; cursor: not-allowed; }
</style>
