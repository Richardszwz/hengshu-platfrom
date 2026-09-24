<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="规则路由">
    <div class="panel">
      <div class="p-head">
        <h3>类型直达规则</h3>
        <span class="sub">客户端以 X-Type 头（或 body.type）请求时按此表改写目标模型；未命中走 body.model</span>
      </div>
      <form class="rule-form" @submit.prevent="add">
        <input v-model.trim="newType" class="inp" placeholder="类型标识，如 code / vl / chat" required />
        <input v-model.trim="newModel" class="inp" placeholder="目标模型名（须在集群在线）" required />
        <button class="btn btn-cyan" type="submit">添加规则</button>
      </form>
      <p v-if="error" class="err">{{ error }}</p>
      <table class="tbl">
        <thead><tr><th>TYPE</th><th>→ MODEL</th><th style="width:70px"></th></tr></thead>
        <tbody>
          <tr v-for="r in rules" :key="r.type">
            <td class="mono cyan">{{ r.type }}</td>
            <td class="mono">{{ r.model }}</td>
            <td><button class="mini-btn danger" @click="del(r.type)">删除</button></td>
          </tr>
          <tr v-if="!rules.length"><td colspan="3" class="empty">暂无规则 — 按类型分流未启用</td></tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'

const rules = ref<any[]>([])
const newType = ref('')
const newModel = ref('')
const error = ref('')

async function load() {
  const d = await api.rules()
  rules.value = d.rules || []
}
async function add() {
  error.value = ''
  try {
    await api.upsertRule(newType.value, newModel.value)
    newType.value = ''; newModel.value = ''
    await load()
  } catch (e: any) { error.value = e?.response?.data?.detail || '添加失败' }
}
async function del(type: string) {
  await api.deleteRule(type)
  await load()
}
onMounted(load)
</script>

<style scoped>
.p-head { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.p-head h3 { margin: 0; font-size: 14px; color: var(--cyan); letter-spacing: 1px; }
.p-head .sub { font-size: 11px; color: var(--txt-dim); }
.rule-form { display: flex; gap: 10px; padding: 12px 14px; }
.inp {
  flex: 1; background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt);
  padding: 8px 10px; font-size: 12px; outline: none;
}
.inp:focus { border-color: var(--cyan-dim); }
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 8px 18px; font-size: 12px; cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.err { color: var(--red); font-size: 12px; padding: 0 14px 8px; margin: 0; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th {
  text-align: left; padding: 8px 14px; color: var(--txt-dim); font-weight: 400;
  border-bottom: 1px solid var(--line); font-size: 10px; letter-spacing: 1px;
}
.tbl td { padding: 8px 14px; border-bottom: 1px solid rgba(0, 200, 255, .06); }
.mono { font-family: var(--mono); }
.cyan { color: var(--cyan); }
.empty { color: var(--txt-dim); text-align: center; padding: 24px !important; }
.mini-btn {
  background: transparent; border: 1px solid var(--line); color: var(--txt-dim);
  font-size: 10px; padding: 2px 8px; cursor: pointer;
}
.mini-btn.danger:hover { color: var(--red); border-color: rgba(255,77,90,.5); }
</style>
