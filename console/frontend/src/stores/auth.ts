import { defineStore } from 'pinia'
import axios from 'axios'

function applyHeader(token: string) {
  if (token) axios.defaults.headers.common.Authorization = `Bearer ${token}`
  else delete axios.defaults.headers.common.Authorization
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const token = localStorage.getItem('hs_token') || ''
    applyHeader(token)  // 刷新页面后恢复鉴权头
    return { token }
  },
  actions: {
    async login(username: string, password: string) {
      const { data } = await axios.post('/api/auth/login', { username, password })
      this.token = data.access_token
      localStorage.setItem('hs_token', this.token)
      applyHeader(this.token)
    },
    logout() {
      this.token = ''
      localStorage.removeItem('hs_token')
      applyHeader('')
    }
  }
})
