<template>
  <div class="app">
    <!-- 侧边导航 -->
    <aside class="side">
      <div class="logo">
        <div class="logo-mark"></div>
        <div><b>衡枢<span style="color:var(--cyan);font-size:12px;letter-spacing:1px"> HENGSHU</span></b><small>GPU CLUSTER LB</small></div>
      </div>
      <nav class="nav">
        <a class="on"><span class="ico"></span>概览驾驶舱</a>
        <a><span class="ico"></span>节点管理<span class="badge" v-if="summary">{{ summary.nodes_online }}/{{ summary.nodes_total }}</span></a>
        <a><span class="ico"></span>模型服务<span class="badge" v-if="summary">{{ summary.services_running }}/{{ summary.services_total }}</span></a>
        <a><span class="ico"></span>租户管理</a>
        <a><span class="ico"></span>规则路由</a>
        <a><span class="ico"></span>审计日志</a>
        <a><span class="ico"></span>系统设置</a>
      </nav>
      <div class="side-foot">
        <div class="avatar">R</div>
        <div><b>admin</b><small>平台管理员 · v0.1.0</small></div>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </aside>

    <!-- 主区 -->
    <div class="main">
      <div class="topbar">
        <div class="sys-state" :class="{ warn: !clusterOk }">
          <span class="blink"></span>{{ clusterOk ? 'SYSTEM ONLINE' : 'NODE DEGRADED' }}
        </div>
        <div class="crumb">概览 / <b>集群驾驶舱</b></div>
        <div class="top-right">
          <button class="live-btn" @click="toggleLive">{{ live ? '⏸ 暂停实时流' : '▶ 恢复实时流' }}</button>
          <div class="bell" @click.stop="showAlerts = !showAlerts"><i v-if="alerts.length"></i></div>
          <div class="clock">{{ clock }}</div>
        </div>
        <div class="alerts" v-if="showAlerts" @click.stop>
          <h4>⚠ 活跃告警 ({{ alerts.length }})</h4>
          <div v-for="(a, i) in alerts" :key="i" class="alert-item" :class="{ err: a.level === 'ERROR' }">
            {{ a.text }}<small>{{ a.time }}</small>
          </div>
          <div v-if="!alerts.length" style="color:var(--txt-dim);font-size:12px;padding:8px">无未确认告警</div>
        </div>
      </div>

      <!-- KPI 行 -->
      <div class="kpis" v-if="summary">
        <div class="panel corner kpi">
          <div class="num">{{ fmtNum(lastQps) }}<small>req/s</small></div>
          <div class="lab">集群实时 QPS</div>
          <div class="delta up">REALTIME · ROUTER</div>
        </div>
        <div class="panel corner kpi">
          <div class="num">{{ summary.sessions }}</div>
          <div class="lab">在线会话 SESSION</div>
          <div class="delta" :class="summary.sessions ? 'up' : ''">{{ summary.sessions ? '粘性绑定中' : '等待首个会话' }}</div>
        </div>
        <div class="panel corner kpi">
          <div class="num">{{ fmtNum(summary.tokens_per_s_total) }}<small>tok/s</small></div>
          <div class="lab">集群吞吐 TOKENS/S</div>
          <div class="delta up">RUNNING ×{{ summary.services_running }}</div>
        </div>
        <div class="panel corner kpi">
          <div class="num">{{ Math.round(summary.ttft_avg) }}<small>ms</small></div>
          <div class="lab">平均 TTFT</div>
          <div class="delta" :class="summary.ttft_avg < 500 ? 'up' : 'dn'">{{ summary.ttft_avg < 500 ? '▼ 优良' : '▲ 偏高' }}</div>
        </div>
        <div class="panel corner kpi">
          <div class="num">{{ summary.nodes_online }}<small>/{{ summary.nodes_total }}</small></div>
          <div class="lab">在线节点 NODES</div>
          <div class="delta" :class="clusterOk ? 'up' : 'dn'">{{ clusterOk ? '全部健康' : '存在离线' }}</div>
        </div>
      </div>

      <!-- 图区 A：QPS 趋势 + 集群水位 -->
      <div class="grid-a">
        <div class="panel corner">
          <div class="p-title">集群 QPS 趋势 <span class="sub">REALTIME · 60s WINDOW</span></div>
          <div ref="elQps" id="chQps" class="chart"></div>
        </div>
        <div class="panel corner">
          <div class="p-title">集群综合水位 <span class="sub">WEIGHTED</span></div>
          <div ref="elGauge" id="chGauge" class="chart"></div>
        </div>
      </div>

      <!-- 图区 B：TTFT / 模型吞吐 / 会话分布 -->
      <div class="grid-b">
        <div class="panel corner">
          <div class="p-title">TTFT 延迟 <span class="sub">ms · AVG</span></div>
          <div ref="elTtft" id="chTtft" class="chart"></div>
        </div>
        <div class="panel corner">
          <div class="p-title">模型吞吐排行 <span class="sub">TOKENS/S</span></div>
          <div ref="elModel" id="chModel" class="chart"></div>
        </div>
        <div class="panel corner">
          <div class="p-title">会话分布 <span class="sub">BY MODEL</span></div>
          <div ref="elSess" id="chSess" class="chart"></div>
        </div>
      </div>

      <!-- 节点卡片 -->
      <div class="nodes">
        <div v-for="n in nodeCards" :key="n.id" class="panel corner node" @click="openNode(n)">
          <div class="n-head"><span class="dot" :class="n.st"></span><b>{{ n.id }}</b><span class="n-gpu">{{ n.gpu }}</span></div>
          <div class="n-models">{{ n.models.join(' · ') || '—' }}</div>
          <div class="bar-row"><span>GPU</span><div class="bar" :class="{ hot: n.util > 85 }"><i :style="{ width: n.util + '%' }"></i></div><em>{{ n.util }}%</em></div>
          <div class="bar-row"><span>VRAM</span><div class="bar" :class="{ hot: n.vram > 85 }"><i :style="{ width: n.vram + '%' }"></i></div><em>{{ n.vram }}%</em></div>
          <div class="n-foot"><span>TTFT <b>{{ n.ttft || '—' }}</b></span><span>TOK/S <b>{{ n.tps }}</b></span><span>FW <b style="font-size:11px">{{ n.fw }}</b></span></div>
        </div>
        <div v-if="!nodeCards.length" class="panel corner" style="grid-column:1/-1;color:var(--txt-dim);font-size:12px">
          等待节点心跳…（启动 scripts/dev-up.sh 或接入真实 Agent）
        </div>
      </div>

      <!-- 图区 C：集群事件流 -->
      <div class="grid-c">
        <div class="panel corner">
          <div class="p-title">集群事件流 <span class="sub">EVENT SYSTEM · ROUTER / AGENT / SCHED</span></div>
          <div ref="elEv" class="ev-box">
            <div v-for="(e, i) in events" :key="i" class="ev-line">
              <span class="t">{{ e.time }}</span><span class="lv" :class="e.level">{{ e.level }}</span><span class="m">{{ e.text }}</span>
            </div>
            <div v-if="!events.length" style="color:#4a6a80;font-size:11px">暂无事件</div>
          </div>
        </div>
      </div>

      <!-- 服务表 -->
      <div class="panel corner svc">
        <div class="p-title">模型服务实例 <span class="sub">{{ services.length }} SERVICES · {{ runningCount }} RUNNING</span></div>
        <table class="hs-table">
          <thead><tr><th>服务</th><th>节点</th><th>框架</th><th>状态</th><th>GPU</th><th>TTFT</th><th>TOKENS/S</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="s in services" :key="s.inst_id">
              <td><b style="color:#fff">{{ s.model }}</b></td>
              <td>{{ s.node }}</td>
              <td style="font-family:Rajdhani">{{ s.framework }}</td>
              <td><span class="tag" :class="s.status === 'running' ? 'g' : 'w'">● {{ s.status === 'running' ? '运行中' : '已停止' }}</span></td>
              <td style="font-family:Rajdhani">{{ s.metrics?.m_gpu_util ?? '—' }}%</td>
              <td style="font-family:Rajdhani">{{ s.metrics?.m_ttft ? Math.round(s.metrics.m_ttft) + 'ms' : '—' }}</td>
              <td style="font-family:Rajdhani;color:var(--cyan)">{{ s.metrics?.m_tps ? fmtNum(s.metrics.m_tps) : '—' }}</td>
              <td>
                <button class="mini-btn" @click="openNode(nodeById(s.node))">详情</button>
                <button class="mini-btn" @click="showLogs(s)">日志</button>
                <button v-if="s.status === 'running'" class="mini-btn danger" @click="stop(s)">停止</button>
                <button v-else class="mini-btn go" @click="start(s)">启动</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 节点详情抽屉 -->
    <div class="mask" :class="{ on: drawerOpen }" @click="closeDrawer"></div>
    <div class="drawer" :class="{ open: drawerOpen }">
      <button class="d-close" @click="closeDrawer">✕</button>
      <h3>{{ (drawerNode?.id || 'NODE').toUpperCase() }}</h3>
      <div class="d-sub">{{ drawerNode?.gpu }} · {{ drawerNode?.fw }} · {{ drawerNode?.arch }}</div>
      <div class="d-kv">
        <div class="panel corner"><div class="num" :style="{ color: drawerNode?.util > 85 ? 'var(--red)' : '#fff' }">{{ drawerNode?.util }}%</div><div class="lab">GPU 利用率</div></div>
        <div class="panel corner"><div class="num">{{ drawerNode?.vram }}%</div><div class="lab">显存占用</div></div>
        <div class="panel corner"><div class="num">{{ drawerNode?.ttft || '—' }}</div><div class="lab">TTFT ms</div></div>
        <div class="panel corner"><div class="num">{{ drawerNode?.tps }}</div><div class="lab">TOKENS/S</div></div>
      </div>
      <div class="panel corner">
        <div class="p-title">近 60s 利用率</div>
        <div ref="elNodeDetail" id="chNodeDetail"></div>
      </div>
      <div class="panel corner" style="margin-top:12px">
        <div class="p-title">运行模型</div>
        <div style="font-size:12px;line-height:2">
          <span v-for="m in drawerNode?.models" :key="m" class="tag g" style="margin-right:8px">{{ m }}</span>
          <span v-if="!drawerNode?.models.length" style="color:var(--txt-dim)">—</span>
        </div>
      </div>
      <div class="panel corner dlog">
        <div class="p-title">节点系统日志 <span class="sub">TAIL · AGENT+FW</span></div>
        <div class="log-box">
          <div v-for="(l, i) in drawerLogs" :key="i" class="log-line">
            <span class="lt">{{ l.t }}</span><span class="lv" :class="l.level">{{ l.level }}</span>
            <span class="lm">[{{ l.src }}] {{ l.msg }}</span><span class="ls">{{ l.src }}</span>
          </div>
          <div v-if="!drawerLogs.length" style="color:#4a6a80;font-size:11px">
            {{ drawerNode ? '正在拉取日志…' : '' }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import * as echarts from 'echarts'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'

/* ---------- 状态 ---------- */
const summary = ref<any>(null)
const services = ref<any[]>([])
const nodeUtils = ref<Record<string, number[]>>({})
const qpsSeries = ref<number[]>([])
const ttftSeries = ref<number[]>([])
const live = ref(true)
const showAlerts = ref(false)
const clock = ref('--:--:--')
const drawerOpen = ref(false)
const drawerNode = ref<any>(null)
const drawerLogs = ref<any[]>([])

const elQps = shallowRef<HTMLElement>()
const elGauge = shallowRef<HTMLElement>()
const elTtft = shallowRef<HTMLElement>()
const elModel = shallowRef<HTMLElement>()
const elSess = shallowRef<HTMLElement>()
const elNodeDetail = shallowRef<HTMLElement>()

const charts = new Map<string, echarts.ECharts>()
const router = useRouter()
const auth = useAuthStore()
let timer = 0
let clockTimer = 0

/* ---------- 计算属性 ---------- */
const events = computed(() => (summary.value?.events || []).slice(-16))
const alerts = computed(() =>
  (summary.value?.events || []).filter((e: any) => e.level === 'WARN' || e.level === 'ERROR').slice(-5))
const clusterOk = computed(() =>
  summary.value ? summary.value.nodes_online === summary.value.nodes_total : true)
const runningCount = computed(() => services.value.filter(s => s.status === 'running').length)
const lastQps = computed(() => qpsSeries.value[qpsSeries.value.length - 1] ?? 0)

const nodeCards = computed(() => {
  const utils = nodeUtils.value
  const addrs = summary.value?.agent_addrs || {}
  return Object.keys(addrs).map(nid => {
  const nodeInsts = services.value.filter(s => s.node === nid)
  const running = nodeInsts.filter(s => s.status === 'running')
  const util = running.length
    ? Math.round(running.reduce((a, s) => a + (s.metrics?.m_gpu_util || 0), 0) / running.length)
    : 0
  const vram = running.length
    ? Math.round(running.reduce((a, s) => a + (s.metrics?.m_vram || 0), 0) / running.length)
    : 0
  const ttft = running.length
    ? Math.round(running.reduce((a, s) => a + (s.metrics?.m_ttft || 0), 0) / running.length)
    : 0
  const tps = Math.round(running.reduce((a, s) => a + (s.metrics?.m_tps || 0), 0))
  return {
    id: nid, util, vram, ttft, tps,
    gpu: summary.value?.node_gpus?.[nid] || (running.length ? 'GPU' : 'idle'),
    fw: running[0]?.framework || '—',
    arch: '',  // arch 由 nodes API 提供，下方合并
    models: nodeInsts.map(s => s.model),
    st: running.length ? 'a' : 's',
    _utilSeries: utils[nid] || []
  }
  })
})

function nodeById(nid: string) {
  return nodeCards.value.find(n => n.id === nid)
}

/* ---------- 格式化 ---------- */
function fmtNum(n: number) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(Math.round(n))
}

/* ---------- ECharts 配置（001 原型样式常量） ---------- */
const AXIS = { line: 'rgba(0,229,255,.25)', split: 'rgba(0,229,255,.08)', label: '#5f7f95' }
const baseAxis = {
  axisLine: { lineStyle: { color: AXIS.line } },
  axisLabel: { color: AXIS.label, fontFamily: 'Rajdhani', fontSize: 11 },
  splitLine: { lineStyle: { color: AXIS.split, type: 'dashed' } },
  axisTick: { show: false }
}
const CY = '#00e5ff', GR = '#2dffa8', AM = '#ffb545', VI = '#8a7bff'
const TIP = {
  trigger: 'axis',
  backgroundColor: 'rgba(5,16,30,.92)', borderColor: AXIS.line,
  textStyle: { color: '#d7ecf5', fontSize: 12 }
}

function ensureChart(key: string, el: HTMLElement | undefined) {
  if (!el) return null
  let c = charts.get(key)
  if (!c) { c = echarts.init(el); charts.set(key, c) }
  return c
}

function renderCharts() {
  const s = summary.value
  if (!s) return

  // QPS 实时流
  ensureChart('qps', elQps.value)?.setOption({
    grid: { left: 44, right: 16, top: 18, bottom: 26 },
    tooltip: TIP,
    xAxis: { type: 'category', data: qpsSeries.value.map((_, i) => i + 's'), ...baseAxis, boundaryGap: false },
    yAxis: { type: 'value', min: 0, ...baseAxis },
    series: [{
      type: 'line', data: qpsSeries.value, smooth: true, symbol: 'none',
      lineStyle: { width: 2, color: CY, shadowColor: 'rgba(0,229,255,.6)', shadowBlur: 12 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(0,229,255,.35)' }, { offset: 1, color: 'rgba(0,229,255,0)' }]) }
    }]
  })

  // 集群水位仪表（双环：GPU + VRAM）
  ensureChart('gauge', elGauge.value)?.setOption({
    series: [
      {
        type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '92%', center: ['50%', '58%'],
        progress: { show: true, width: 12, itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#0077ff' }, { offset: 1, color: CY }]), shadowColor: 'rgba(0,229,255,.8)', shadowBlur: 14 } },
        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(255,255,255,.06)']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, anchor: { show: false },
        title: { show: true, offsetCenter: [0, '32%'], color: AXIS.label, fontSize: 12 },
        detail: { valueAnimation: true, offsetCenter: [0, '-2%'], fontSize: 44, fontFamily: 'Rajdhani', fontWeight: 700, color: '#fff', formatter: '{value}%' },
        data: [{ value: s.gpu_util_weighted, name: 'GPU UTIL 加权' }]
      },
      {
        type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '68%', center: ['50%', '58%'],
        progress: { show: true, width: 8, itemStyle: { color: GR } },
        axisLine: { lineStyle: { width: 8, color: [[1, 'rgba(255,255,255,.06)']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, anchor: { show: false },
        title: { show: true, offsetCenter: [0, '36%'], color: AXIS.label, fontSize: 11 },
        detail: { offsetCenter: [0, '2%'], fontSize: 26, fontFamily: 'Rajdhani', fontWeight: 700, color: GR, formatter: '{value}%' },
        data: [{ value: s.vram_weighted, name: 'VRAM 加权' }]
      }
    ]
  })

  // TTFT 均值曲线
  ensureChart('ttft', elTtft.value)?.setOption({
    grid: { left: 44, right: 16, top: 18, bottom: 26 },
    tooltip: TIP,
    xAxis: { type: 'category', data: ttftSeries.value.map((_, i) => i + 's'), ...baseAxis, boundaryGap: false },
    yAxis: { type: 'value', ...baseAxis },
    series: [{
      type: 'line', data: ttftSeries.value, smooth: true, symbol: 'none',
      lineStyle: { color: CY, width: 2 }, areaStyle: { color: 'rgba(0,229,255,.12)' },
      markLine: {
        silent: true, symbol: 'none', lineStyle: { color: AM, type: 'dashed' },
        label: { color: AM, fontFamily: 'Rajdhani', formatter: 'BUDGET 500' }, data: [{ yAxis: 500 }]
      }
    }]
  })

  // 模型吞吐排行（真实数据）
  const byModel: Record<string, number> = {}
  for (const svc of services.value) {
    if (svc.status !== 'running') continue
    byModel[svc.model] = (byModel[svc.model] || 0) + (svc.metrics?.m_tps || 0)
  }
  const mNames = Object.keys(byModel).sort((a, b) => byModel[a] - byModel[b])
  const mColors = ['#0077ff', CY, GR, VI, AM, '#ff4d6a']
  ensureChart('model', elModel.value)?.setOption({
    grid: { left: 8, right: 44, top: 10, bottom: 26, containLabel: true },
    tooltip: { ...TIP, trigger: 'item' },
    xAxis: { type: 'value', ...baseAxis },
    yAxis: { type: 'category', data: mNames, ...baseAxis },
    series: [{
      type: 'bar', barWidth: 12,
      data: mNames.map((n, i) => ({ value: Math.round(byModel[n]), itemStyle: { color: mColors[i % mColors.length] } })),
      itemStyle: { borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', color: AXIS.label, fontFamily: 'Rajdhani', fontSize: 12 },
      showBackground: true, backgroundStyle: { color: 'rgba(255,255,255,.03)' }
    }]
  })

  // 会话分布（真实会话按模型聚合）
  const sess = s.sess_by_model || {}
  const sNames = Object.keys(sess)
  const sessColors = [CY, GR, VI, AM]
  const sessTotal = s.sessions || 0
  const centerLabel = {
    show: true, position: 'center',
    formatter: `{a|${sessTotal}}\n{b|在线会话}`,
    rich: { a: { color: '#fff', fontSize: 30, fontFamily: 'Rajdhani', fontWeight: 700 }, b: { color: AXIS.label, fontSize: 11, padding: [4, 0, 0, 0] } }
  }
  ensureChart('sess', elSess.value)?.setOption({
    tooltip: { ...TIP, trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, textStyle: { color: AXIS.label, fontSize: 11 }, itemWidth: 10, itemHeight: 10, show: sNames.length > 0 },
    series: [{
      type: 'pie', radius: ['52%', '74%'], center: ['50%', '44%'],
      itemStyle: { borderColor: '#040a14', borderWidth: 3, borderRadius: 4 },
      label: centerLabel,
      data: sNames.map((n, i) => ({ value: sess[n], name: n, itemStyle: { color: sessColors[i % sessColors.length] } }))
    }]
  })

  // 抽屉内节点 60s 利用率
  if (drawerOpen.value && drawerNode.value) {
    const series = (drawerNode.value as any)._utilSeries || []
    ensureChart('detail', elNodeDetail.value)?.setOption({
      grid: { left: 36, right: 12, top: 14, bottom: 22 },
      xAxis: { type: 'category', data: series.map((_: number, i: number) => i + 's'), ...baseAxis, boundaryGap: false },
      yAxis: { type: 'value', max: 100, ...baseAxis },
      series: [{
        type: 'line', smooth: true, symbol: 'none', data: series,
        lineStyle: { color: (drawerNode.value as any).st === 'a' ? CY : 'rgba(255,255,255,.3)', width: 2 },
        areaStyle: { color: 'rgba(0,229,255,.15)' }
      }]
    })
  }
}

/* ---------- 数据刷新 ---------- */
async function refresh() {
  if (!live.value) return
  try {
    const [s, sv, ser] = await Promise.all([api.summary(), api.services(), api.series(60)])
    summary.value = s
    services.value = sv.services
    qpsSeries.value = ser.qps
    ttftSeries.value = ser.ttft
    nodeUtils.value = ser.node_utils
    renderCharts()
    if (drawerOpen.value && drawerNode.value) {
      const nid = (drawerNode.value as any).id
      const card = nodeCards.value.find(n => n.id === nid)
      if (card) drawerNode.value = card
    }
  } catch (e) {
    console.error('[hengshu] refresh failed:', e)  // 控制台可见，勿静默吞错
  }
}

/* ---------- 抽屉 ---------- */
async function openNode(n: any) {
  if (!n) return
  drawerNode.value = n
  drawerLogs.value = []
  drawerOpen.value = true
  showAlerts.value = false
  await nextTick()
  renderCharts()
  // 拉取节点上第一个服务的日志（真实链路：console → agent）
  const svc = services.value.find(x => x.node === n.id)
  if (svc) {
    try {
      const d = await api.serviceLogs(n.id, svc.model, 40)
      drawerLogs.value = d.lines || []
    } catch {
      drawerLogs.value = []
    }
  }
}

function closeDrawer() {
  drawerOpen.value = false
}

async function showLogs(s: any) {
  openNode(nodeById(s.node))
}

/* ---------- 服务操作 ---------- */
async function stop(s: any) {
  await api.stopService(s.node, s.model)
  await refresh()
}
async function start(s: any) {
  await api.startService(s.node, s.model)
  await refresh()
}

function toggleLive() {
  live.value = !live.value
  if (live.value) refresh()
}

function logout() {
  auth.logout()
  router.push('/login')
}

/* ---------- 生命周期 ---------- */
function tickClock() {
  clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 1000)
  refresh()
  timer = window.setInterval(refresh, 3000)
  window.addEventListener('resize', () => charts.forEach(c => c.resize()))
})
onUnmounted(() => {
  clearInterval(timer)
  clearInterval(clockTimer)
  charts.forEach(c => c.dispose())
  charts.clear()
})
</script>
