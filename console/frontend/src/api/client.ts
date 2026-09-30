import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import router from '../router'

// 401（token 过期/失效）→ 清登录态并弹回登录页，避免表单里冒 cryptic 报错
axios.interceptors.response.use(
  (r) => r,
  (err) => {
    const url = err?.config?.url || ''
    if (err?.response?.status === 401 && !url.includes('/api/auth/login')) {
      const auth = useAuthStore()
      if (auth.token) {
        auth.logout()
        router.push({ name: 'login' })
      }
    }
    return Promise.reject(err)
  }
)

export const api = {
  summary: () => axios.get('/api/dashboard/summary').then(r => r.data),
  series: (points = 60) =>
    axios.get('/api/dashboard/series', { params: { points } }).then(r => r.data),
  nodes: () => axios.get('/api/nodes').then(r => r.data),
  addNode: (payload: object) => axios.post('/api/nodes', payload).then(r => r.data),
  installNode: (nodeId: string, payload: object) =>
    axios.post(`/api/nodes/${nodeId}/install`, payload).then(r => r.data),
  removeNode: (nodeId: string) => axios.delete(`/api/nodes/${nodeId}`).then(r => r.data),
  // 审计：事件流 + 终端录屏
  auditsEvents: () => axios.get('/api/audits/events').then(r => r.data),
  recordings: () => axios.get('/api/term/recordings').then(r => r.data),
  // 系统设置
  settingsGet: () => axios.get('/api/settings').then(r => r.data),
  settingsSetWebhook: (url: string) => axios.put('/api/settings/webhook', { url }).then(r => r.data),
  settingsTestWebhook: () => axios.post('/api/settings/webhook/test').then(r => r.data),
  settingsChangePwd: (old_password: string, new_password: string) =>
    axios.post('/api/settings/password', { old_password, new_password }).then(r => r.data),
  services: () => axios.get('/api/services').then(r => r.data),
  launchService: (node: string, payload: object) =>
    axios.post(`/api/services/${node}/start`, payload).then(r => r.data),
  stopService: (node: string, name: string) =>
    axios.post(`/api/services/${node}/${name}/stop`).then(r => r.data),
  startService: (node: string, name: string) =>
    axios.post(`/api/services/${node}/${name}/start`).then(r => r.data),
  serviceLogs: (node: string, name: string, tail = 100) =>
    axios.get(`/api/services/${node}/${name}/logs`, { params: { tail } }).then(r => r.data),
  tenants: () => axios.get('/api/tenants').then(r => r.data),
  createTenant: (payload: object) => axios.post('/api/tenants', payload).then(r => r.data),
  deleteTenant: (id: string) => axios.delete(`/api/tenants/${id}`).then(r => r.data),
  rules: () => axios.get('/api/rules').then(r => r.data),
  upsertRule: (type: string, model: string) =>
    axios.put(`/api/rules/${encodeURIComponent(type)}`, null, { params: { model } }).then(r => r.data),
  deleteRule: (type: string) =>
    axios.delete(`/api/rules/${encodeURIComponent(type)}`).then(r => r.data)
}
