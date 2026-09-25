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
            <tr v-for="n in nodes" :key="n.node_id">
              <td class="id-cell">{{ n.node_id }}</td>
              <td>{{ n.hostname }} <span class="dim" v-if="n.hostname">:{{ n.ssh_port || 22 }}</span></td>
              <td class="mono">{{ n.arch || '—' }}</td>
              <td>
                <span class="st" :class="n.status === 'online' ? 'ok' : (n.status === 'pending' ? 'wait' : 'bad')">
                  <i></i>{{ n.status === 'online' ? '在线' : (n.status === 'pending' ? '待上线' : '离线') }}
                </span>
              </td>
              <td class="mono">{{ n.instances ?? 0 }}</td>
              <td class="dim">{{ fmtAgo(n.last_seen) }}</td>
              <td><button class="op danger" @click="remove(n)">删除</button></td>
            </tr>
            <tr v-if="!nodes.length">
              <td colspan="7" class="empty">暂无节点 — 点击右上角「添加节点」开始纳管</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="svc-dim">提示：删除仅清除登记与凭证；目标机上的 Agent 容器需 SSH 手动 docker rm -f hengshu-agent</p>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api/client'
import AppLayout from '../components/AppLayout.vue'

const nodes = ref<any[]>([])
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
  } catch (e) { console.error(e) }
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
  if (!confirm(`删除节点 ${n.node_id}？（仅删除登记与凭证，目标机 Agent 容器需手动清理）`)) return
  try {
    await api.removeNode(n.node_id)
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.message || '删除失败'
    alert(msg)
  }
  refresh()
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 4000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
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
.svc-dim { color: var(--txt-faint); font-size: 11px; margin-top: 8px; }
</style>
