<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="系统设置">
    <!-- 运行信息（只读） -->
    <div class="panel">
      <div class="p-head"><h3>运行信息</h3><span class="sub">部署级参数由环境变量决定，此处只读展示</span></div>
      <div class="info-grid" v-if="info">
        <div class="info-item"><span class="k">平台版本</span><span class="v mono">衡枢 HENGSHU v{{ info.platform_version }}</span></div>
        <div class="info-item"><span class="k">Router 端口</span><span class="v mono">:{{ info.router_port }}</span></div>
        <div class="info-item"><span class="k">Router 管理员 Key</span>
          <span class="v" :class="info.router_api_key_set ? 'ok' : 'warn'">{{ info.router_api_key_set ? '已配置' : '未配置（ROUTER_API_KEY）' }}</span></div>
        <div class="info-item"><span class="k">Redis</span>
          <span class="v" :class="info.redis_ok ? 'ok' : 'bad'">{{ info.redis_ok ? '连接正常' : '连接失败' }}</span></div>
        <div class="info-item"><span class="k">登录态有效期</span><span class="v">{{ info.token_ttl_hours }} 小时</span></div>
        <div class="info-item"><span class="k">气隙 tar 目录</span><span class="v mono dim">{{ info.install_tar_dir || '未启用（仓库模式）' }}</span></div>
      </div>
    </div>

    <!-- 授权（两层 License） -->
    <div class="panel">
      <div class="p-head">
        <h3>授权</h3>
        <span class="sub">软件基本 License + 路由引擎 License · 气隙离线激活</span>
      </div>
      <div class="info-grid" v-if="lic">
        <div class="info-item"><span class="k">状态</span>
          <span class="v" :class="lic.ok ? 'ok' : 'bad'">{{ lic.ok ? '已激活' : ('未激活' + (lic.reason && lic.reason !== 'missing' ? `（${lic.reason}）` : '')) }}</span></div>
        <div class="info-item" v-if="lic.ok"><span class="k">客户</span><span class="v">{{ lic.customer }}</span></div>
        <div class="info-item" v-if="lic.ok"><span class="k">基本 License</span>
          <span class="v">{{ lic.base_mode === 'perpetual' ? '买断制' : '订阅制' }}<template v-if="lic.base_expires"> · {{ fmtDate(lic.base_expires) }} 到期</template>{{ lic.base_in_grace ? '（宽限期）' : '' }}</span></div>
        <div class="info-item" v-if="lic.ok"><span class="k">节点上限</span><span class="v mono">{{ lic.max_nodes || '∞' }}</span></div>
        <div class="info-item" v-if="lic.ok"><span class="k">引擎版本</span>
          <span class="v"><b :class="lic.edition === 'STD' ? '' : 'ok'">{{ lic.edition }}</b>
            <span class="dim">（{{ lic.caps.join(', ') }}）</span></span></div>
        <div class="info-item"><span class="k">集群指纹</span>
          <span class="v mono" style="user-select:all;cursor:text">{{ lic.fingerprint }}</span></div>
      </div>
      <div class="row" style="margin-top:10px">
        <textarea v-model.trim="licText" class="inp lic-inp" rows="4"
          placeholder='粘贴 license JSON（把上方指纹发给商务换回 license，粘贴到此点击激活）'></textarea>
      </div>
      <div class="row">
        <button class="btn" :disabled="busy || !licText" @click="activateLic">激活</button>
        <button v-if="lic && lic.ok" class="btn ghost" :disabled="busy" @click="deactivateLic">移除授权</button>
        <button class="btn ghost" @click="loadLic">刷新状态</button>
      </div>
      <p v-if="msg.lic" class="msg" :class="msg.licOk ? 'ok' : 'bad'">{{ msg.lic }}</p>
    </div>

    <!-- 语义引擎（embedding）—— PRO/ENT 授权才可操作 -->
    <div class="panel">
      <div class="p-head">
        <h3>语义引擎（embedding） <span class="eng-badge">HSE</span></h3>
        <span class="sub">Ollama 框架 · 无则自动安装并拉取模型 · 接入后语义路由可用</span>
      </div>
      <div v-if="!enginePro" class="locked-tip">
        🔒 需导入 <b>PRO / ENT</b> 引擎 License 后启用（上方「授权」区激活）
      </div>
      <template v-else>
        <div class="row">
          <span class="k" style="align-self:center">状态</span>
          <span v-if="embedStatus.url" class="embed-state" :class="{ ok: embedStatus.ok }">
            {{ embedStatus.ok ? '● 已接入' : '● 异常' }} <span class="mono">{{ embedStatus.url }}</span>
            <template v-if="!embedStatus.ok && embedStatus.note">（{{ embedStatus.note }}）</template>
          </span>
          <span v-else class="embed-state">○ 未部署（语义规则停用中）</span>
        </div>
        <div class="row">
          <span class="k" style="align-self:center">部署到</span>
          <select v-model="deploy.target" class="inp sel">
            <option value="local">本机（衡枢控制台所在机）</option>
            <option v-for="n in nodeList" :key="n.id" :value="n.id">{{ n.id }}（{{ n.ip }}）</option>
          </select>
          <span class="k" style="align-self:center">API 端口</span>
          <input v-model.number="deploy.port" class="inp" style="flex:0 0 90px" type="number" />
          <span class="k" style="align-self:center">模型</span>
          <input v-model.trim="deploy.model" class="inp" style="flex:0 0 130px" placeholder="bge-m3" />
        </div>
        <div class="row">
          <span class="k" style="align-self:center">模型存放位置</span>
          <input v-model.trim="deploy.models_dir" class="inp"
                 placeholder="留空=Ollama 默认（~/.ollama/models）；自定义如 /data/ollama-models" />
        </div>
        <div class="row" v-if="deploy.target !== 'local'">
          <span class="k" style="align-self:center">SSH 用户</span>
          <input v-model.trim="deploy.ssh_user" class="inp" style="flex:0 0 120px" />
          <span class="k" style="align-self:center">密码/私钥</span>
          <input v-model="deploy.ssh_pass" class="inp" type="password"
                 placeholder="本次部署用，不落库；私钥请粘 PEM 全文" />
        </div>
        <div class="row">
          <button class="btn" :disabled="embedStatus.deploying" @click="doDeploy">
            {{ embedStatus.deploying ? '部署中…（进度见事件流）' : '部署并接入' }}</button>
          <span class="dim" style="align-self:center;font-size:11px">已有可用端点？直接填：</span>
          <input v-model.trim="cfg.embedding_url" class="inp" placeholder="http://host:11434" />
          <button class="btn ghost" @click="saveCfg">接入</button>
        </div>
        <div class="row">
          <span class="k" style="align-self:center">α 激进度</span>
          <input type="range" min="0" max="1" step="0.05" v-model.number="cfg.alpha" style="flex:1" />
          <b class="mono" style="color:var(--cyan)">{{ cfg.alpha.toFixed(2) }}</b>
          <button class="btn ghost" @click="saveCfg">保存 α</button>
          <span class="dim" style="align-self:center;font-size:11px">越高语义路由越激进</span>
        </div>
        <!-- 裁判（ENT）：小 LLM YES/NO 判定，borderline 请求的最终仲裁 -->
        <div class="row" style="border-top:1px dashed var(--line)">
          <span class="k" style="align-self:center">裁判（ENT）</span>
          <template v-if="engineEnt">
            <input v-model.trim="cfg.judge_url" class="inp" placeholder="裁判端点，如 http://192.168.66.50:8100（OpenAI 兼容 chat）" />
            <input v-model.trim="cfg.judge_model" class="inp" style="flex:0 0 150px" placeholder="裁判模型名" />
            <button class="btn ghost" @click="saveCfg">保存裁判</button>
          </template>
          <span v-else class="dim" style="align-self:center;font-size:11px">🔒 需 ENT License</span>
        </div>
        <p v-if="deployMsg || cfgMsg" class="msg" :class="embedStatus.ok ? 'ok' : ''">{{ deployMsg || cfgMsg }}</p>
      </template>
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

    <!-- 用户管理（admin；非 admin 后端 403，前端自动隐藏） -->
    <div class="panel" v-if="usersVisible">
      <div class="p-head">
        <h3>用户管理</h3>
        <span class="sub">viewer 只读 · operator 运维节点/服务/规则/租户 · admin 全部</span>
      </div>
      <form class="row" @submit.prevent="createUser">
        <input v-model.trim="nu.username" class="inp" placeholder="用户名（字母/数字/_/-）" required />
        <input v-model="nu.password" class="inp" type="password" placeholder="初始密码（≥6 位）" required />
        <select v-model="nu.role" class="inp sel">
          <option value="viewer">viewer 只读</option>
          <option value="operator">operator 运维</option>
          <option value="admin">admin 管理员</option>
        </select>
        <button class="btn" type="submit" :disabled="busy">创建用户</button>
      </form>
      <p v-if="msg.users" class="msg bad">{{ msg.users }}</p>
      <table class="tbl">
        <thead>
          <tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>最近登录</th><th style="width:170px"></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.username">
            <td class="mono" style="color:var(--cyan)">{{ u.username }}</td>
            <td>
              <select class="mini-sel" :value="u.role" :disabled="u.username === me"
                      @change="setRole(u, ($event.target as HTMLSelectElement).value)">
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </td>
            <td><span :class="u.enabled ? 'ok-t' : 'bad-t'">{{ u.enabled ? '启用' : '禁用' }}</span></td>
            <td class="mono dim">{{ u.created ? new Date(u.created * 1000).toLocaleString() : '-' }}</td>
            <td class="mono dim">{{ u.last_login ? new Date(u.last_login * 1000).toLocaleString() : '从未' }}</td>
            <td>
              <button class="mini-btn" :disabled="u.username === me" @click="toggleUser(u)">
                {{ u.enabled ? '禁用' : '启用' }}
              </button>
              <button class="mini-btn danger" :disabled="u.username === me" @click="delUser(u)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
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
const msg = reactive({ webhook: '', webhookOk: false, pwd: '', pwdOk: false, users: '', lic: '', licOk: false })
const lic = ref<any>(null)
const licText = ref('')
const enginePro = computed(() => !!lic.value?.ok && lic.value?.edition && lic.value.edition !== 'STD')
const engineEnt = computed(() => !!lic.value?.ok && lic.value?.edition === 'ENT')

/* ---------- 语义引擎（embedding，PRO/ENT 门控） ---------- */
const cfg = reactive({ embedding_url: '', embedding_model: 'bge-m3', alpha: 0.5,
                       judge_url: '', judge_model: '' })
const cfgMsg = ref('')
const embedStatus = reactive({ url: '', model: 'bge-m3', ok: false, note: '', deploying: false, step: '' })
const deploy = reactive({ target: 'local', port: 11434, model: 'bge-m3', models_dir: '', ssh_user: '', ssh_pass: '' })
const deployMsg = ref('')
const nodeList = ref<any[]>([])

async function cfgLoad() {
  try {
    const d = await api.hseConfig()
    Object.assign(cfg, d.config ?? {})
  } catch { /* STD 未授权时 403/404，忽略 */ }
}
async function embedStatusLoad() {
  try {
    const d = await api.hseEmbedStatus()
    Object.assign(embedStatus, d)
  } catch { /* STD 403，保持未部署态 */ }
}
async function nodesLoad() {
  try {
    const d = await api.nodes()
    nodeList.value = (d.nodes ?? [])
      .filter((n: any) => n.status === 'online')
      .map((n: any) => ({ id: n.node_id, ip: n.ssh_host }))
  } catch { /* 忽略 */ }
}
async function saveCfg() {
  cfgMsg.value = ''
  try {
    await api.hsePutConfig({ embedding_url: cfg.embedding_url,
                             embedding_model: cfg.embedding_model, alpha: cfg.alpha,
                             judge_url: cfg.judge_url, judge_model: cfg.judge_model })
    cfgMsg.value = '已保存（引擎 5s 内热更）'
    await embedStatusLoad()
  } catch (e: any) {
    cfgMsg.value = e?.response?.data?.detail || '保存失败'
  }
}
async function doDeploy() {
  deployMsg.value = ''
  if (deploy.target !== 'local' && (!deploy.ssh_user || !deploy.ssh_pass)) {
    deployMsg.value = '部署到节点需填 SSH 用户与密码/私钥（不落库）'
    return
  }
  try {
    const body: any = { target: deploy.target, port: deploy.port,
                        model: deploy.model, models_dir: deploy.models_dir }
    if (deploy.target !== 'local') {
      const isPem = deploy.ssh_pass.includes('PRIVATE KEY')
      body.ssh = { username: deploy.ssh_user,
                   ...(isPem ? { private_key: deploy.ssh_pass } : { password: deploy.ssh_pass }) }
    }
    await api.hseEmbedDeploy(body)
    deployMsg.value = '已受理，后台部署中（拉模型需数分钟，进度见事件流）'
    const t = setInterval(async () => {
      await embedStatusLoad()
      if (!embedStatus.deploying) {
        clearInterval(t)
        deployMsg.value = embedStatus.ok ? '✓ 已接入 ' + embedStatus.url
          : ('部署结束：' + (embedStatus.note || embedStatus.step))
      }
    }, 3000)
  } catch (e: any) {
    deployMsg.value = e?.response?.data?.detail || '部署请求失败'
  }
}
function fmtDate(ts: number) {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
async function loadLic() {
  try { lic.value = await api.licenseStatus() } catch { /* 401 统一处理 */ }
}
async function activateLic() {
  busy.value = true
  msg.lic = ''
  try {
    await api.licenseActivate(licText.value)
    msg.lic = '激活成功'
    msg.licOk = true
    licText.value = ''
    await loadLic()
    if (enginePro.value) { cfgLoad(); embedStatusLoad(); nodesLoad() }
  } catch (e: any) {
    msg.lic = e?.response?.data?.detail || '激活失败'
    msg.licOk = false
  } finally {
    busy.value = false
  }
}
async function deactivateLic() {
  busy.value = true
  try {
    await api.licenseDeactivate()
    lic.value = null
    msg.lic = '已移除授权'
    msg.licOk = true
    await loadLic()
  } finally {
    busy.value = false
  }
}
const pwd = reactive({ old: '', next: '', confirm: '' })
const refreshMs = ref(parseInt(localStorage.getItem('hs_refresh_ms') || '4000'))
const auth = useAuthStore()
const router = useRouter()
const me = auth.username || 'admin'

/* ---------- 用户管理（admin） ---------- */
const users = ref<any[]>([])
const usersVisible = ref(false)
const nu = reactive({ username: '', password: '', role: 'viewer' })

async function loadUsers() {
  try {
    users.value = (await api.usersList()).users
    usersVisible.value = true
  } catch { usersVisible.value = false }  // 非 admin 隐藏
}

async function createUser() {
  msg.users = ''
  busy.value = true
  try {
    await api.usersCreate(nu.username, nu.password, nu.role)
    nu.username = ''; nu.password = ''; nu.role = 'viewer'
    await loadUsers()
  } catch (e: any) {
    msg.users = e?.response?.data?.detail || '创建失败'
  } finally { busy.value = false }
}

async function setRole(u: any, role: string) {
  try { await api.usersUpdate(u.username, { role }); await loadUsers() }
  catch (e: any) { msg.users = e?.response?.data?.detail || '更新失败' }
}

async function toggleUser(u: any) {
  try { await api.usersUpdate(u.username, { enabled: !u.enabled }); await loadUsers() }
  catch (e: any) { msg.users = e?.response?.data?.detail || '操作失败' }
}

async function delUser(u: any) {
  if (!confirm(`删除用户 ${u.username}？其登录态立即失效`)) return
  try { await api.usersDelete(u.username); await loadUsers() }
  catch (e: any) { msg.users = e?.response?.data?.detail || '删除失败' }
}

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

onMounted(async () => {
  load(); loadUsers()
  await loadLic()
  if (enginePro.value) { cfgLoad(); embedStatusLoad(); nodesLoad() }
})
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
/* 用户管理表 */
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th { text-align: left; padding: 8px 14px; color: var(--txt-dim); border-bottom: 1px solid var(--line); font-weight: 500; }
.tbl td { padding: 8px 14px; border-bottom: 1px solid var(--line); }
.mini-sel { background: rgba(0, 30, 50, .6); border: 1px solid var(--line); color: var(--txt); padding: 3px 6px; font-size: 11px; }
.mini-btn { background: transparent; border: 1px solid var(--line); color: var(--txt-dim); font-size: 11px; padding: 3px 10px; cursor: pointer; margin-right: 6px; }
.mini-btn.danger:hover { color: var(--red); border-color: rgba(255, 77, 90, .4); }
.mini-btn:disabled { opacity: .35; cursor: not-allowed; }
.ok-t { color: var(--green); } .bad-t { color: var(--red); }
.lic-inp { width: 100%; font-family: var(--font-mono, monospace); font-size: 11px; resize: vertical; }
.eng-badge { font-size: 10px; border: 1px solid var(--cyan-dim); color: var(--cyan); padding: 0 5px; }
.locked-tip { padding: 18px 14px; font-size: 12px; color: var(--txt-dim); }
.locked-tip b { color: var(--yellow); }
.embed-state { font-size: 12px; color: var(--txt-dim); }
.embed-state.ok { color: var(--green); }
</style>
