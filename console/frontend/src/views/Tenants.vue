<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="租户管理">
    <!-- 对外接入信息：客户端从这里拿地址与用法 -->
    <div class="panel api-panel">
      <div class="p-head">
        <h3>对外 API 接入</h3>
        <span class="sub">OpenAI 兼容统一入口 · 业务方凭下方 Key 调用</span>
      </div>
      <div class="api-grid">
        <div class="api-item">
          <span class="k">接入地址</span>
          <code class="mono endpoint">{{ apiBase }}</code>
        </div>
        <div class="api-item">
          <span class="k">认证方式</span>
          <span class="v">请求头 <code class="mono">Authorization: Bearer &lt;API Key&gt;</code>（下方租户 Key 或管理员 Key）</span>
        </div>
        <div class="api-item">
          <span class="k">调用示例</span>
          <pre class="mono curl"># ① 全自动（推荐）：不带 model，按负载自动选择实例
curl {{ apiBase }}/chat/completions \
  -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}]}'

# ② 规则路由：带 X-Type 头，命中规则映射到指定模型
curl {{ apiBase }}/chat/completions \
  -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
  -H "X-Type: code" \
  -d '{"messages":[{"role":"user","content":"hi"}]}'

# ③ 显式指定模型
curl {{ apiBase }}/chat/completions \
  -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-8B","messages":[{"role":"user","content":"hi"}]}'</pre>
        </div>
        <div class="api-item">
          <span class="k">规则路由</span>
          <span class="v">请求头 <code class="mono">X-Type: &lt;类型&gt;</code> 命中「规则路由」页映射时改写目标模型</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="p-head">
        <h3>多租户</h3>
        <span class="sub">API Key 限流与用量隔离（P2 实装限流执行；当前 Key 仅创建时展示一次）</span>
      </div>
      <form class="t-form" @submit.prevent="create">
        <input v-model.trim="newId" class="inp" placeholder="租户标识（小写字母/数字/-_）" required />
        <input v-model.number="newQps" class="inp qps" type="number" min="1" max="10000" placeholder="QPS 上限" />
        <button class="btn btn-cyan" type="submit">创建租户</button>
      </form>
      <div v-if="createdKey" class="key-banner">
        <b>{{ createdId }}</b> 的 API Key：<code class="mono">{{ createdKey }}</code>
        <span class="warn">（仅展示一次，请立即保存）</span>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
      <table class="tbl">
        <thead>
          <tr><th>租户</th><th>QPS 上限</th><th>累计 TOKENS</th><th>创建时间</th><th style="width:70px"></th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tenants" :key="t.tenant_id">
            <td class="mono cyan">{{ t.tenant_id }}</td>
            <td class="mono">{{ t.qps_limit || '-' }}</td>
            <td class="mono">{{ t.tokens_total || '0' }}</td>
            <td class="mono dim">{{ t.created ? new Date(+t.created * 1000).toLocaleString() : '-' }}</td>
            <td><button class="mini-btn danger" @click="del(t.tenant_id)">删除</button></td>
          </tr>
          <tr v-if="!tenants.length"><td colspan="5" class="empty">暂无租户</td></tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'

const tenants = ref<any[]>([])
const newId = ref('')
const newQps = ref(10)
const createdKey = ref('')
const createdId = ref('')
const error = ref('')

// 对外 API 统一入口（Router 端口默认 9000，随部署环境变量变化）
const apiBase = `${location.protocol}//${location.hostname}:9000/v1`

async function load() {
  const d = await api.tenants()
  tenants.value = d.tenants || []
}
async function create() {
  error.value = ''; createdKey.value = ''
  try {
    const d = await api.createTenant({ tenant_id: newId.value, qps_limit: newQps.value })
    createdKey.value = d.api_key; createdId.value = d.tenant_id
    newId.value = ''
    await load()
  } catch (e: any) { error.value = e?.response?.data?.detail || '创建失败' }
}
async function del(id: string) {
  if (!confirm(`删除租户 ${id}？其 API Key 将立即失效`)) return
  await api.deleteTenant(id)
  await load()
}
onMounted(load)
</script>

<style scoped>
.api-panel { margin-bottom: 12px; }
.api-grid { padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.api-item { display: flex; gap: 12px; align-items: baseline; font-size: 12px; }
.api-item .k { flex: 0 0 72px; color: var(--txt-dim); font-size: 11px; letter-spacing: 1px; }
.api-item .endpoint { color: var(--cyan); font-size: 13px; }
.api-item .v { color: var(--txt); }
.api-item .curl {
  margin: 0; padding: 8px 10px; background: rgba(0, 30, 50, .6);
  border: 1px solid var(--line); font-size: 11px; color: var(--txt); white-space: pre-wrap;
}
.mono { font-family: var(--mono); }
.p-head { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.p-head h3 { margin: 0; font-size: 14px; color: var(--cyan); letter-spacing: 1px; }
.p-head .sub { font-size: 11px; color: var(--txt-dim); }
.t-form { display: flex; gap: 10px; padding: 12px 14px; }
.inp {
  flex: 1; background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt);
  padding: 8px 10px; font-size: 12px; outline: none;
}
.inp.qps { flex: 0 0 120px; }
.inp:focus { border-color: var(--cyan-dim); }
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 8px 18px; font-size: 12px; cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.key-banner {
  margin: 0 14px 10px; padding: 10px 12px; font-size: 12px;
  border: 1px solid rgba(255, 200, 87, .4); background: rgba(255, 200, 87, .06);
}
.key-banner code { color: var(--yellow); user-select: all; }
.key-banner .warn { color: var(--txt-dim); }
.err { color: var(--red); font-size: 12px; padding: 0 14px 8px; margin: 0; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th {
  text-align: left; padding: 8px 14px; color: var(--txt-dim); font-weight: 400;
  border-bottom: 1px solid var(--line); font-size: 10px; letter-spacing: 1px;
}
.tbl td { padding: 8px 14px; border-bottom: 1px solid rgba(0, 200, 255, .06); }
.mono { font-family: var(--mono); }
.cyan { color: var(--cyan); }
.dim { color: var(--txt-dim); }
.empty { color: var(--txt-dim); text-align: center; padding: 24px !important; }
.mini-btn {
  background: transparent; border: 1px solid var(--line); color: var(--txt-dim);
  font-size: 10px; padding: 2px 8px; cursor: pointer;
}
.mini-btn.danger:hover { color: var(--red); border-color: rgba(255,77,90,.5); }
</style>
