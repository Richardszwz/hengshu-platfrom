<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="系统设置">
    <!-- 运行信息（只读） -->
    <div class="panel">
      <div class="p-head"><h3>运行信息</h3><span class="sub">部署级参数由环境变量决定，此处只读展示</span></div>
      <div class="info-grid" v-if="info">
        <div class="info-item"><span class="k">Router 端口</span><span class="v mono">:{{ info.router_port }}</span></div>
        <div class="info-item"><span class="k">Router 管理员 Key</span>
          <span class="v" :class="info.router_api_key_set ? 'ok' : 'warn'">{{ info.router_api_key_set ? '已配置' : '未配置（ROUTER_API_KEY）' }}</span></div>
        <div class="info-item"><span class="k">Redis</span>
          <span class="v" :class="info.redis_ok ? 'ok' : 'bad'">{{ info.redis_ok ? '连接正常' : '连接失败' }}</span></div>
        <div class="info-item"><span class="k">登录态有效期</span><span class="v">{{ info.token_ttl_hours }} 小时</span></div>
        <div class="info-item"><span class="k">气隙 tar 目录</span><span class="v mono dim">{{ info.install_tar_dir || '未启用（仓库模式）' }}</span></div>
      </div>
    </div>

    <!-- Webhook 通知 -->
    <div class="panel">
      <div class="p-head">
        <h3>Webhook 通知</h3>
        <span class="sub">节点安装结果 / 终端高危拦截 → POST JSON。留空=关闭。来源：{{ sourceText }}</span>
      </div>
      <div class="row">
        <input v-model.trim="webhookUrl" class="inp" placeholder="https://example.com/hook（留空关闭通知）" />
        <button class="btn" :disabled="busy" @click="saveWebhook">保存</button>
        <button class="btn ghost" :disabled="busy || !webhookUrl" @click="testWebhook">发送测试</button>
      </div>
      <p v-if="msg.webhook" class="msg" :class="msg.webhookOk ? 'ok' : 'bad'">{{ msg.webhook }}</p>
    </div>

    <!-- 修改管理员密码 -->
    <div class="panel">
      <div class="p-head"><h3>修改管理员密码</h3><span class="sub">立即生效（Redis 覆盖环境变量哈希）；改后需重新登录</span></div>
      <div class="row">
        <input v-model="pwd.old" class="inp" type="password" placeholder="原密码" />
        <input v-model="pwd.next" class="inp" type="password" placeholder="新密码（≥6 位）" />
        <input v-model="pwd.confirm" class="inp" type="password" placeholder="确认新密码" />
        <button class="btn" :disabled="busy" @click="changePwd">修改</button>
      </div>
      <p v-if="msg.pwd" class="msg" :class="msg.pwdOk ? 'ok' : 'bad'">{{ msg.pwd }}</p>
    </div>

    <!-- 显示偏好 -->
    <div class="panel">
      <div class="p-head"><h3>显示偏好</h3><span class="sub">保存在本浏览器</span></div>
      <div class="row">
        <span class="k" style="align-self:center">驾驶舱刷新间隔</span>
        <select v-model="refreshMs" class="inp sel" @change="saveRefresh">
          <option :value="2000">2 秒</option>
          <option :value="4000">4 秒（默认）</option>
          <option :value="10000">10 秒</option>
          <option :value="30000">30 秒</option>
        </select>
        <span class="dim" style="align-self:center;font-size:11px">下次进入驾驶舱生效</span>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useRouter } from 'vue-router'

const info = ref<any>(null)
const webhookUrl = ref('')
const webhookSource = ref('none')
const busy = ref(false)
const msg = reactive({ webhook: '', webhookOk: false, pwd: '', pwdOk: false })
const pwd = reactive({ old: '', next: '', confirm: '' })
const refreshMs = ref(parseInt(localStorage.getItem('hs_refresh_ms') || '4000'))
const auth = useAuthStore()
const router = useRouter()

const sourceText = computed(() =>
  ({ redis: '页面设置（覆盖环境变量）', env: '环境变量', none: '未配置' } as any)[webhookSource.value] || webhookSource.value)

async function load() {
  info.value = await api.settingsGet()
  webhookUrl.value = info.value.webhook_url || ''
  webhookSource.value = info.value.webhook_source
}

async function saveWebhook() {
  busy.value = true
  try {
    await api.settingsSetWebhook(webhookUrl.value)
    await load()
    msg.webhook = webhookUrl.value ? '已保存，立即生效' : '已关闭通知'
    msg.webhookOk = true
  } catch (e: any) {
    msg.webhook = e?.response?.data?.detail || '保存失败'
    msg.webhookOk = false
  } finally { busy.value = false }
}

async function testWebhook() {
  busy.value = true
  try {
    await api.settingsTestWebhook()
    msg.webhook = '测试事件已发送（请查收目标服务）'
    msg.webhookOk = true
  } catch (e: any) {
    msg.webhook = e?.response?.data?.detail || '发送失败'
    msg.webhookOk = false
  } finally { busy.value = false }
}

async function changePwd() {
  msg.pwd = ''
  if (pwd.next.length < 6) { msg.pwd = '新密码至少 6 位'; msg.pwdOk = false; return }
  if (pwd.next !== pwd.confirm) { msg.pwd = '两次输入的新密码不一致'; msg.pwdOk = false; return }
  busy.value = true
  try {
    await api.changeMyPwd(pwd.old, pwd.next)
    msg.pwd = '已更新，2 秒后跳转登录页'
    msg.pwdOk = true
    setTimeout(() => { auth.logout(); router.push({ name: 'login' }) }, 2000)
  } catch (e: any) {
    msg.pwd = e?.response?.data?.detail || '修改失败'
    msg.pwdOk = false
  } finally { busy.value = false }
}

function saveRefresh() {
  localStorage.setItem('hs_refresh_ms', String(refreshMs.value))
}

onMounted(load)
</script>

<style scoped>
.panel { background: var(--card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 14px; }
.p-head { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.p-head h3 { margin: 0; font-size: 14px; color: var(--cyan); letter-spacing: 1px; }
.p-head .sub { font-size: 11px; color: var(--txt-dim); margin-left: 8px; }
.info-grid { padding: 12px 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 24px; }
.info-item { display: flex; gap: 12px; font-size: 12px; }
.info-item .k { flex: 0 0 110px; color: var(--txt-dim); font-size: 11px; letter-spacing: 1px; }
.v.ok { color: var(--green); } .v.warn { color: var(--yellow); } .v.bad { color: var(--red); }
.dim { color: var(--txt-dim); }
.row { display: flex; gap: 10px; padding: 12px 14px; }
.inp {
  flex: 1; background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt);
  padding: 8px 10px; font-size: 12px; outline: none;
}
.inp.sel { flex: 0 0 160px; }
.inp:focus { border-color: var(--cyan-dim); }
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 8px 18px; font-size: 12px; cursor: pointer; white-space: nowrap;
}
.btn.ghost { background: transparent; color: var(--txt-dim); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.msg { margin: 0 14px 12px; font-size: 12px; }
.msg.ok { color: var(--green); } .msg.bad { color: var(--red); }
.mono { font-family: var(--mono); }
</style>
