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
