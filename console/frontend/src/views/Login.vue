<template>
  <div class="login-wrap">
    <div class="panel corner login-panel">
      <div class="logo">
        <div class="logo-mark"></div>
        <div><b>衡枢<span style="color:var(--cyan);font-size:12px;letter-spacing:1px"> HENGSHU</span></b><small>GPU CLUSTER LB</small></div>
      </div>
      <p class="login-slogan">万流归一 · 衡以分之</p>
      <form @submit.prevent="doLogin">
        <input v-model="username" class="hs-input" placeholder="用户名" autocomplete="username" />
        <input v-model="password" class="hs-input" type="password" placeholder="密码"
               autocomplete="current-password" />
        <button class="hs-btn" type="submit" :disabled="loading">{{ loading ? '验证中…' : '登 录' }}</button>
      </form>
      <div v-if="error" class="login-err">{{ error }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const username = ref('admin')
const password = ref('')
const loading = ref(false)
const error = ref('')
const auth = useAuthStore()
const router = useRouter()

async function doLogin() {
  loading.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/')
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '登录失败：请检查密码'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  position: relative; z-index: 1;
}
.login-panel { width: 360px; padding: 28px 30px; }
.login-panel .logo { border-bottom: none; padding: 0 0 6px; justify-content: center; }
.login-slogan {
  text-align: center; color: var(--txt-dim); font-size: 12px; letter-spacing: 6px;
  margin: 4px 0 22px;
}
.login-panel form { display: flex; flex-direction: column; gap: 12px; }
.hs-input {
  background: #08131e; border: 1px solid var(--line); color: var(--txt);
  padding: 10px 12px; font-size: 13px; outline: none;
}
.hs-input:focus { border-color: var(--cyan); box-shadow: 0 0 8px rgba(0, 229, 255, .2); }
.hs-btn {
  background: linear-gradient(90deg, var(--cyan), #2a86b8); color: #04121e; border: 0;
  padding: 10px; font-weight: 700; letter-spacing: 6px; cursor: pointer; font-size: 14px;
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
}
.hs-btn:disabled { opacity: .6; cursor: wait; }
.login-err {
  margin-top: 12px; color: var(--red); font-size: 12px; text-align: center;
  border: 1px solid rgba(255, 77, 106, .35); background: rgba(255, 77, 106, .08); padding: 6px;
}
</style>
