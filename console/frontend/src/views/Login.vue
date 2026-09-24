<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h1 class="brand">衡枢 <span>HENGSHU</span></h1>
      <p class="slogan">万流归一，衡以分之</p>
      <el-form @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码"
                    size="large" show-password @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" size="large" style="width:100%" :loading="loading"
                   @click="doLogin">登 录</el-button>
      </el-form>
      <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top:12px" />
    </el-card>
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
    error.value = e?.response?.data?.detail || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100vh; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(ellipse at 30% 20%, rgba(0,229,255,0.08), transparent 60%), #0a0e1a;
}
.login-card { width: 360px; background: #101828; border: 1px solid rgba(0,229,255,0.2); }
.brand { color: #00e5ff; font-size: 30px; margin: 0 0 4px; letter-spacing: 6px; }
.brand span { font-size: 14px; color: #8a94a8; letter-spacing: 3px; }
.slogan { color: #8a94a8; font-size: 12px; letter-spacing: 4px; margin: 0 0 24px; }
</style>
