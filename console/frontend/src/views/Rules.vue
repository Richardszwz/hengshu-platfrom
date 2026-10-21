<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="规则路由">
    <div class="panel">
      <div class="p-head">
        <h3>类型直达规则</h3>
        <span class="sub">客户端以 X-Type 头（或 body.type）请求时按此表改写目标模型；未命中走 body.model</span>
      </div>
      <form class="rule-form" @submit.prevent="add">
        <input v-model.trim="newType" class="inp" placeholder="类型标识，如 code / vl / chat" required />
        <select v-if="onlineModels.length" v-model="newModel" class="inp" required>
          <option value="" disabled selected>选择目标模型（集群在线）</option>
          <option v-for="m in onlineModels" :key="m" :value="m">{{ m }}</option>
        </select>
        <input v-else v-model.trim="newModel" class="inp" placeholder="目标模型名（暂无在线服务，手动输入）" required />
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
    <!-- HSE 智能路由规则（引擎求值：接口类型未命中且未指定模型时） -->
    <div class="panel">
      <div class="p-head">
        <h3>智能路由规则 <span class="hse-badge">HSE</span></h3>
        <span class="sub">优先级大者先命中；条件全部满足才命中（AND）；未命中走集群负载均衡。引擎失联自动降级，不影响主链路</span>
      </div>
      <form class="hse-form" @submit.prevent="hseAdd">
        <div class="hf-row">
          <input v-model.trim="hse.name" class="inp" placeholder="规则名，如：代码类去 27B" required />
          <input v-model.number="hse.priority" class="inp prio" type="number" placeholder="优先级" title="优先级（大者先）" />
          <label class="chk"><input type="checkbox" v-model="hse.enabled" /> 启用</label>
        </div>
        <div class="hf-row">
          <input v-model.trim="hse.keywords" class="inp"
                 placeholder="关键字（逗号分隔，任一命中；留空=不检）" />
          <select v-model="hse.scope" class="inp sm" title="关键字作用域">
            <option value="last_user">仅最后一轮提问</option>
            <option value="all">全部上下文</option>
          </select>
        </div>
        <div class="hf-row">
          <input v-model="hse.ctxMin" class="inp sm2" type="number" placeholder="ctx≥（token）" title="上下文 token 下限，留空不限" />
          <input v-model="hse.ctxMax" class="inp sm2" type="number" placeholder="ctx≤（token）" title="上下文 token 上限，留空不限" />
          <select v-model="hse.codeBlock" class="inp sm" title="是否含代码块">
            <option value="">代码块不限</option>
            <option value="true">必须含代码块</option>
            <option value="false">不含代码块</option>
          </select>
          <select v-model="hse.lang" class="inp sm" title="提问语言">
            <option value="">语言不限</option>
            <option value="zh">中文</option>
            <option value="en">英文</option>
          </select>
        </div>
        <div class="hf-row">
          <select v-if="onlineModels.length" v-model="hse.model" class="inp">
            <option value="">目标模型（不限/走档位）</option>
            <option v-for="m in onlineModels" :key="m" :value="m">{{ m }}</option>
          </select>
          <input v-else v-model.trim="hse.model" class="inp" placeholder="目标模型名（暂无在线，手动输入；可留空走档位）" />
          <select v-model="hse.tier" class="inp sm" title="档位别名（STD）" :disabled="!!hse.model">
            <option value="">档位不限</option>
            <option value="fast">fast（快模型）</option>
            <option value="medium">medium</option>
            <option value="strong">strong（强模型）</option>
          </select>
          <button class="btn btn-cyan" type="submit">添加</button>
        </div>
        <!-- PRO：语义条件（例句 KNN，bge-m3 本地 embedding；部署在 系统设置 → 语义引擎） -->
        <div class="hf-row sem-row">
          <textarea v-model="hse.examples" class="inp sem-inp" rows="2" :disabled="!semanticOk"
            :placeholder="semanticOk
              ? '语义例句（PRO，每行一条，3-5 条最佳；提问与例句语义相近即命中）\n如：帮我写代码 / 调试这个报错 / 解释一下这段函数'
              : '🔒 语义条件需 PRO/ENT License（系统设置 → 授权），并在 系统设置 → 语义引擎 部署 embedding'" />
          <input v-model="hse.threshold" class="inp sm2" type="number" step="0.01" min="0.3" max="0.95"
                 placeholder="阈值" title="语义阈值（留空=模型阈值>默认0.62）" :disabled="!semanticOk" />
          <label class="chk"><input type="checkbox" v-model="hse.shadow" title="命中只记录不执行" /> 影子</label>
        </div>
      </form>
      <p v-if="hseError" class="err">{{ hseError }}</p>
      <table class="tbl">
        <thead><tr>
          <th style="width:70px">优先级</th><th>规则</th><th>条件</th><th>→ 目标</th>
          <th style="width:60px">状态</th><th style="width:70px"></th>
        </tr></thead>
        <tbody>
          <tr v-for="r in hseRules" :key="r.id">
            <td class="mono">{{ r.priority }}</td>
            <td>{{ r.name }}<span v-if="r.shadow" class="shadow-tag" title="影子模式：命中只记录不执行">👻影子</span></td>
            <td class="dim" style="font-size:11px">{{ condText(r.when) }}</td>
            <td class="mono cyan">{{ r.action?.model || r.action?.tier || '—' }}</td>
            <td>
              <button class="mini-btn" :class="{ danger: !r.enabled }" @click="hseToggle(r)">
                {{ r.enabled ? '启用' : '停用' }}
              </button>
            </td>
            <td><button class="mini-btn danger" @click="hseDel(r.id)">删除</button></td>
          </tr>
          <tr v-if="!hseRules.length"><td colspan="6" class="empty">暂无智能规则 — 未指定模型的请求全部走集群负载均衡</td></tr>
        </tbody>
      </table>
      <!-- 模型参数：同优先级命中时成本小者优先（threshold 为 PRO 语义路由预留） -->
      <div class="modelcfg" v-if="onlineModels.length">
        <p class="mc-title">模型参数<span class="dim">（同优先级命中 → 成本小者优先；置信度阈值为 PRO 语义路由预留）</span></p>
        <div class="mc-row" v-for="m in onlineModels" :key="m">
          <span class="mono mc-name">{{ m }}</span>
          <label>成本权重 <input class="inp mc-inp" type="number" step="0.1" min="0"
            :value="modelcfg[m]?.cost ?? 0" @change="e => setModelcfg(m, 'cost', e)" /></label>
          <label class="dim">置信度阈值 <input class="inp mc-inp" type="number" step="0.05" min="0" max="1"
            :value="modelcfg[m]?.threshold ?? 0" disabled title="PRO 语义路由启用" /></label>
        </div>
      </div>
      <!-- 引擎配置区已移至 系统设置 → 语义引擎（PRO/ENT 门控） -->
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'

const rules = ref<any[]>([])
const newType = ref('')
const newModel = ref('')
const error = ref('')
const onlineModels = ref<string[]>([])

/* ---------- HSE 智能路由规则 ---------- */
const hseRules = ref<any[]>([])
const hseError = ref('')
const hse = reactive({
  name: '', priority: 100, enabled: true,
  keywords: '', scope: 'last_user',
  ctxMin: '' as any, ctxMax: '' as any,
  codeBlock: '', lang: '', model: '', tier: '',
  examples: '', threshold: '' as any, shadow: false
})

/* ---------- 引擎配置（PRO）已移至系统设置页 ---------- */
const semanticOk = ref(false)  // PRO/ENT 授权 + 已接入 embedding 才可填语义条件
async function semanticGateLoad() {
  try {
    const lic = await api.licenseStatus()
    const pro = !!lic?.ok && lic?.edition && lic.edition !== 'STD'
    if (!pro) { semanticOk.value = false; return }
    const st = await api.hseEmbedStatus()
    semanticOk.value = !!st.ok
  } catch { semanticOk.value = false }
}

function condText(w: any): string {
  const parts: string[] = []
  if (w?.keywords?.length) parts.push(`关键字[${w.keywords.join('/')}]${w.keyword_scope === 'all' ? '·全文' : ''}`)
  if (w?.ctx_min != null) parts.push(`ctx≥${w.ctx_min}`)
  if (w?.ctx_max != null) parts.push(`ctx≤${w.ctx_max}`)
  if (w?.has_code_block === true) parts.push('含代码块')
  if (w?.has_code_block === false) parts.push('无代码块')
  if (w?.lang) parts.push(w.lang === 'zh' ? '中文' : '英文')
  if (w?.semantic?.examples?.length) parts.push(`语义[${w.semantic.examples.length}例句${w.semantic.threshold ? '·阈值' + w.semantic.threshold : ''}]`)
  return parts.join(' ∧ ') || '无条件'
}

async function hseLoad() {
  try {
    const d = await api.hseRules()
    hseRules.value = (d.rules ?? []).slice().sort((a: any, b: any) => b.priority - a.priority)
  } catch { /* 引擎规则接口失败不阻塞页面 */ }
}

/* ---------- 模型参数（cost/threshold） ---------- */
const modelcfg = ref<Record<string, { cost: number; threshold: number }>>({})

async function modelcfgLoad() {
  try {
    const d = await api.hseModelcfg()
    const saved = d.models ?? {}
    const out: Record<string, { cost: number; threshold: number }> = {}
    for (const m of onlineModels.value) {
      out[m] = { cost: saved[m]?.cost ?? 0, threshold: saved[m]?.threshold ?? 0 }
    }
    modelcfg.value = out
  } catch { /* 忽略 */ }
}

async function setModelcfg(m: string, key: 'cost' | 'threshold', e: Event) {
  const v = Number((e.target as HTMLInputElement).value) || 0
  const cur = modelcfg.value[m] ?? { cost: 0, threshold: 0 }
  cur[key] = v
  modelcfg.value = { ...modelcfg.value, [m]: cur }
  await api.hsePutModelcfg(m, { cost: cur.cost ?? 0, threshold: cur.threshold ?? 0 })
}

async function hseAdd() {
  hseError.value = ''
  const when: any = {}
  const kws = hse.keywords.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean)
  if (kws.length) { when.keywords = kws; when.keyword_scope = hse.scope }
  if (hse.ctxMin !== '' && hse.ctxMin != null) when.ctx_min = Number(hse.ctxMin)
  if (hse.ctxMax !== '' && hse.ctxMax != null) when.ctx_max = Number(hse.ctxMax)
  if (hse.codeBlock) when.has_code_block = hse.codeBlock === 'true'
  if (hse.lang) when.lang = hse.lang
  const exs = hse.examples.split('\n').map((s: string) => s.trim()).filter(Boolean)
  if (exs.length) {
    when.semantic = { examples: exs }
    if (hse.threshold !== '' && hse.threshold != null) when.semantic.threshold = Number(hse.threshold)
  }
  const action: any = {}
  if (hse.model) action.model = hse.model
  else if (hse.tier) action.tier = hse.tier
  if (!action.model && !action.tier) { hseError.value = '请选目标模型或档位'; return }
  try {
    await api.hseCreateRule({ name: hse.name, enabled: hse.enabled,
                              priority: hse.priority ?? 100, when, action,
                              shadow: hse.shadow })
    hse.name = ''; hse.keywords = ''; hse.ctxMin = ''; hse.ctxMax = ''
    hse.codeBlock = ''; hse.lang = ''; hse.model = ''; hse.tier = ''
    hse.examples = ''; hse.threshold = ''; hse.shadow = false
    await hseLoad()
  } catch (e: any) { hseError.value = e?.response?.data?.detail || '添加失败' }
}

async function hseToggle(r: any) {
  await api.hseUpdateRule(r.id, { name: r.name, enabled: !r.enabled,
                                  priority: r.priority, when: r.when, action: r.action })
  await hseLoad()
}

async function hseDel(id: string) {
  await api.hseDeleteRule(id)
  await hseLoad()
}

async function load() {
  const d = await api.rules()
  rules.value = d.rules ?? Object.entries(d).map(([type, model]) => ({ type, model }))
  try {
    const svcs = await api.services()
    onlineModels.value = [...new Set((svcs.services ?? []).map((s: any) => s.model))].sort()
    await modelcfgLoad()
  } catch { /* 服务列表失败不阻塞规则页 */ }
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
onMounted(() => { load(); hseLoad(); semanticGateLoad() })
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
.hse-badge {
  font-size: 10px; border: 1px solid var(--cyan); color: var(--cyan);
  padding: 1px 6px; margin-left: 6px; letter-spacing: 1px;
}
.hse-form { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
.hf-row { display: flex; gap: 8px; align-items: center; }
.hf-row .inp { flex: 1; }
.hf-row .inp.prio { flex: 0 0 90px; }
.hf-row .inp.sm { flex: 0 0 170px; }
.hf-row .inp.sm2 { flex: 0 0 120px; }
.chk { font-size: 11px; color: var(--txt-dim); display: flex; align-items: center; gap: 4px; white-space: nowrap; }
.modelcfg { border-top: 1px dashed var(--line); padding: 10px 14px; }
.mc-title { font-size: 12px; margin: 0 0 8px; }
.mc-row { display: flex; align-items: center; gap: 16px; margin-bottom: 6px; font-size: 11px; color: var(--txt-dim); }
.mc-name { color: var(--cyan); min-width: 200px; }
.mc-inp { width: 70px; margin-left: 6px; }
.sem-row { align-items: flex-start; }
.sem-inp { flex: 1; font-size: 11px; resize: vertical; font-family: inherit; }
.sem-inp:disabled { opacity: .5; }
.shadow-tag { font-size: 10px; color: var(--yellow, #ffc857); margin-left: 6px; }
</style>
