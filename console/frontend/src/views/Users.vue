<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="用户管理">
    <div class="panel">
      <div class="p-head">
        <h3>控制台账户</h3>
        <span class="sub">viewer 只读 · operator 可运维节点/服务/规则/租户 · admin 全部（含用户/设置/Web终端）</span>
      </div>
      <form class="u-form" @submit.prevent="create">
        <input v-model.trim="nu.username" class="inp" placeholder="用户名（字母/数字/_/-）" required />
        <input v-model="nu.password" class="inp" type="password" placeholder="初始密码（≥6 位）" required />
        <select v-model="nu.role" class="inp sel">
          <option value="viewer">viewer 只读</option>
          <option value="operator">operator 运维</option>
          <option value="admin">admin 管理员</option>
        </select>
        <button class="btn" type="submit" :disabled="busy">创建用户</button>
      </form>
      <p v-if="error" class="err">{{ error }}</p>
      <table class="tbl">
        <thead>
          <tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>最近登录</th><th style="width:210px"></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.username">
            <td class="mono cyan">{{ u.username }}</td>
            <td>
              <select class="mini-sel" :value="u.role" :disabled="u.username === me"
                      @change="setRole(u, ($event.target as HTMLSelectElement).value)">
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </td>
            <td><span class="st" :class="u.enabled ? 'ok' : 'bad'">{{ u.enabled ? '启用' : '禁用' }}</span></td>
            <td class="mono dim">{{ u.created ? new Date(u.created * 1000).toLocaleString() : '-' }}</td>
            <td class="mono dim">{{ u.last_login ? new Date(u.last_login * 1000).toLocaleString() : '从未' }}</td>
            <td>
              <button class="mini-btn" :disabled="u.username === me" @click="toggle(u)">
                {{ u.enabled ? '禁用' : '启用' }}
              </button>
              <button class="mini-btn danger" :disabled="u.username === me" @click="del(u)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'

const users = ref<any[]>([])
const nu = reactive({ username: '', password: '', role: 'viewer' })
const error = ref('')
const busy = ref(false)
const me = useAuthStore().username || 'admin'

async function load() {
  try {
    users.value = (await api.usersList()).users
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载失败（需 admin 角色）'
  }
}

async function create() {
  error.value = ''
  busy.value = true
  try {
    await api.usersCreate(nu.username, nu.password, nu.role)
    nu.username = ''; nu.password = ''; nu.role = 'viewer'
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '创建失败'
  } finally { busy.value = false }
}

async function setRole(u: any, role: string) {
  try { await api.usersUpdate(u.username, { role }); await load() }
  catch (e: any) { error.value = e?.response?.data?.detail || '更新失败' }
}

async function toggle(u: any) {
  try { await api.usersUpdate(u.username, { enabled: !u.enabled }); await load() }
  catch (e: any) { error.value = e?.response?.data?.detail || '操作失败' }
}

async function del(u: any) {
  if (!confirm(`删除用户 ${u.username}？其登录态立即失效`)) return
  try { await api.usersDelete(u.username); await load() }
  catch (e: any) { error.value = e?.response?.data?.detail || '删除失败' }
}

onMounted(load)
</script>

<style scoped>
.panel { background: var(--card); border: 1px solid var(--border); border-radius: 8px; }
.p-head { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.p-head h3 { margin: 0; font-size: 14px; color: var(--cyan); letter-spacing: 1px; }
.p-head .sub { font-size: 11px; color: var(--txt-dim); margin-left: 8px; }
.u-form { display: flex; gap: 10px; padding: 12px 14px; }
.inp {
  flex: 1; background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt);
  padding: 8px 10px; font-size: 12px; outline: none;
}
.inp.sel { flex: 0 0 170px; }
.inp:focus { border-color: var(--cyan-dim); }
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 8px 18px; font-size: 12px; cursor: pointer; white-space: nowrap;
}
.btn:disabled { opacity: .5; }
.err { margin: 0 14px 10px; font-size: 12px; color: var(--red); }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th { text-align: left; padding: 8px 14px; color: var(--txt-dim); border-bottom: 1px solid var(--line); font-weight: 500; }
.tbl td { padding: 8px 14px; border-bottom: 1px solid var(--line); }
.mono { font-family: var(--mono); }
.cyan { color: var(--cyan); }
.dim { color: var(--txt-dim); }
.st.ok { color: var(--green); } .st.bad { color: var(--red); }
.mini-sel {
  background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt);
  padding: 3px 6px; font-size: 11px;
}
.mini-btn {
  background: transparent; border: 1px solid var(--line); color: var(--txt-dim);
  font-size: 11px; padding: 3px 10px; cursor: pointer; margin-right: 6px;
}
.mini-btn.danger:hover { color: var(--red); border-color: rgba(255, 77, 90, .4); }
.mini-btn:disabled { opacity: .35; cursor: not-allowed; }
</style>
