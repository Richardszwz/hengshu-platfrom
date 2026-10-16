<template>
  <AppLayout crumb-prefix="概览" crumb-title="集群驾驶舱" :degraded="!clusterOk">
    <template #top-right>
      <button class="live-btn" @click="toggleLive">{{ live ? '⏸ 暂停实时流' : '▶ 恢复实时流' }}</button>
      <div class="bell" @click.stop="showAlerts = !showAlerts"><i v-if="alerts.length"></i></div>
      <div class="alerts" v-if="showAlerts" @click.stop>
          <h4>⚠ 活跃告警 ({{ alerts.length }})</h4>
          <div v-for="(a, i) in alerts" :key="i" class="alert-item" :class="{ err: a.level === 'ERROR' }">
            {{ a.text }}<small>{{ a.time }}</small>
          </div>
          <div v-if="!alerts.length" style="color:var(--txt-dim);font-size:12px;padding:8px">无未确认告警</div>
        </div>
    </template>

    <div class="dash-wrap">
    <div class="dash-main">
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

      <!-- 图区 A：QPS 趋势 + 集群水位（同排，水位占 1/4 与图区B列等宽） -->
      <div class="grid-a">
        <div class="panel corner">
          <div class="p-title">集群 QPS 趋势 <span class="sub">REALTIME · 60s WINDOW</span></div>
          <div ref="elQps" id="chQps" class="chart"></div>
        </div>
        <div class="panel corner">
          <div class="p-title">集群综合水位 <span class="sub">WEIGHTED</span></div>
          <div class="gauge-wrap">
            <div ref="elGauge" id="chGauge" class="chart"></div>
            <div class="gauge-legend" v-if="summary">
              <span class="gl-item"><i class="dot" style="background:#00e5ff"></i>GPU UTIL 加权
                <b class="mono">{{ Math.round(summary.gpu_util_weighted || 0) }}%</b></span>
              <span class="gl-item"><i class="dot" style="background:#2dffa8"></i>VRAM 加权
                <b class="mono">{{ Math.round(summary.vram_weighted || 0) }}%</b></span>
            </div>
          </div>
        </div>
      </div>

      <!-- 图区 B：TTFT / 模型吞吐 / 会话分布（三等列） -->
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

      <!-- 图区 C 已迁往右侧独占列 -->

      <!-- 服务表 -->
      <div class="panel corner svc">
        <div class="p-title">模型服务实例 <span class="sub">{{ services.length }} SERVICES · {{ runningCount }} RUNNING · 监视只读（操作在节点管理）</span>
        </div>
        <table class="hs-table">
          <thead><tr><th>服务</th><th>节点</th><th>框架</th><th>状态</th><th>GPU</th><th>TTFT</th><th>TOKENS/S</th></tr></thead>
          <tbody>
            <tr v-for="s in services" :key="s.inst_id">
              <td><b style="color:#fff">{{ s.model }}</b></td>
              <td>{{ s.node }}</td>
              <td style="font-family:Rajdhani">{{ s.framework }}</td>
              <td><span class="tag" :class="s.status === 'running' ? 'g' : (s.status === 'crashing' ? 'r' : 'w')">
                ● {{ s.status === 'running' ? '运行中' : (s.status === 'crashing' ? '崩溃循环' : '已停止') }}</span></td>
              <td style="font-family:Rajdhani">{{ s.metrics?.m_gpu_util ?? '—' }}%</td>
              <td style="font-family:Rajdhani">{{ s.metrics?.m_ttft ? Math.round(s.metrics.m_ttft) + 'ms' : '—' }}</td>
              <td style="font-family:Rajdhani;color:var(--cyan)">{{ s.metrics?.m_tps ? fmtNum(s.metrics.m_tps) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

    </div><!-- /dash-main -->

    <!-- 右侧独占列：集群事件流（最新在上） -->
    <div class="dash-side">
      <div class="panel corner ev-col">
        <div class="p-title">集群事件流 <span class="sub">EVENT SYSTEM · 最新在上</span></div>
        <div ref="elEv" class="ev-box">
          <div v-for="(e, i) in events" :key="i" class="ev-line">
            <span class="t">{{ e.time }}</span><span class="lv" :class="e.level">{{ e.level }}</span><span class="m">{{ e.text }}</span>
          </div>
          <div v-if="!events.length" style="color:#4a6a80;font-size:11px">暂无事件</div>
        </div>
      </div>
    </div>
    </div><!-- /dash-wrap -->

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
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api/client'
import AppLayout from '../components/AppLayout.vue'

/* ---------- 状态 ---------- */
const summary = ref<any>(null)
const services = ref<any[]>([])
const nodeUtils = ref<Record<string, number[]>>({})
const qpsSeries = ref<number[]>([])
const ttftSeries = ref<number[]>([])
const live = ref(true)
const showAlerts = ref(false)
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
let timer = 0

/* ---------- 计算属性 ---------- */
const events = computed(() => [...(summary.value?.events || [])].reverse().slice(0, 40))  // 最新在上
const alerts = computed(() => {
  const evts = (summary.value?.events || []).filter((e: any) => e.level === 'WARN' || e.level === 'ERROR')
  // 崩溃循环服务置顶告警（用户要求：报错要打到前台）
  const crashing = services.value.filter(s => s.status === 'crashing').map(s => ({
    level: 'ERROR', text: `服务 ${s.model}@${s.node} 崩溃循环中（重启多次）`, time: '现在',
  }))
  return [...crashing, ...evts].slice(-8)
})
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
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  if (n > 0 && n < 10) return n.toFixed(1)   // 小流量 QPS（0.1~9.9）必须可见，不能四舍五入成 0
  return String(Math.round(n))
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

  // 集群水位仪表（双环：GPU + VRAM；文字改 HTML 覆盖层，结构上不可能重叠）
  ensureChart('gauge', elGauge.value)?.setOption({
    series: [
      {
        type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '92%', center: ['50%', '58%'],
        progress: { show: true, width: 12, itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#0077ff' }, { offset: 1, color: CY }]), shadowColor: 'rgba(0,229,255,.8)', shadowBlur: 14 } },
        axisLine: { lineStyle: { width: 12, color: [[1, 'rgba(255,255,255,.06)']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, anchor: { show: false },
        title: { show: false }, detail: { show: false },
        data: [{ value: s.gpu_util_weighted, name: 'GPU UTIL 加权' }]
      },
      {
        type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '68%', center: ['50%', '58%'],
        progress: { show: true, width: 8, itemStyle: { color: GR } },
        axisLine: { lineStyle: { width: 8, color: [[1, 'rgba(255,255,255,.06)']] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, anchor: { show: false },
        title: { show: false }, detail: { show: false },
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
  // 节点系统日志 = Agent 生命周期事件（受理/拉镜像/成败）+ 首个服务的容器日志
  const lines: any[] = []
  try {
    const nl = await api.nodeLogs(n.id, 60)
    for (const e of (nl.lines || [])) {
      const d = new Date(e.ts * 1000)
      const p = (x: number) => String(x).padStart(2, '0')
      lines.push({ t: `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`,
                   level: e.level, msg: e.msg, src: 'agent' })
    }
  } catch { /* 节点离线时无日志源 */ }
  const svc = services.value.find(x => x.node === n.id)
  if (svc) {
    try {
      const d = await api.serviceLogs(n.id, svc.model, 40)
      for (const l of (d.lines || [])) lines.push(l)
    } catch { /* 服务日志拉取失败不阻塞 */ }
  }
  drawerLogs.value = lines
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

async function delService(s: any) {
  if (!confirm(`删除服务 ${s.model}（节点 ${s.node}）？容器将停止并移除登记，不可恢复`)) return
  try {
    const d = await api.deleteService(s.node, s.model)
    if (typeof d.agent === 'string' && d.agent.includes('不可达')) alert(d.agent)
  } catch (e: any) {
    alert(e?.response?.data?.detail || '删除失败')
  }
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

/* ---------- 生命周期 ---------- */
onMounted(() => {
  refresh()
  // 刷新间隔可在「系统设置 → 显示偏好」调整
  const iv = parseInt(localStorage.getItem('hs_refresh_ms') || '3000')
  timer = window.setInterval(refresh, isNaN(iv) ? 3000 : iv)
  window.addEventListener('resize', () => charts.forEach(c => c.resize()))
})
onUnmounted(() => {
  clearInterval(timer)
  charts.forEach(c => c.dispose())
  charts.clear()
})
</script>

<style scoped>
/* 布局框架样式在 AppLayout.vue；此处为驾驶舱专属（001 原型还原） */

/* ---------- 顶栏右侧控件 ---------- */
.live-btn {
  background: linear-gradient(90deg, rgba(0,229,255,.15), rgba(0,229,255,.05));
  border: 1px solid var(--cyan-dim); color: var(--cyan); font-size: 11px;
  padding: 5px 12px; cursor: pointer; letter-spacing: 1px;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.live-btn:hover { background: rgba(0,229,255,.25); }
.bell { position: relative; width: 16px; height: 16px; border: 1.5px solid var(--txt-dim); border-radius: 8px 8px 3px 3px; cursor: pointer; }
.bell i { position: absolute; top: -4px; right: -4px; width: 7px; height: 7px; border-radius: 50%; background: var(--red); box-shadow: 0 0 6px var(--red); }
.alerts {
  position: absolute; top: 38px; right: 0; width: 300px; background: var(--panel-solid);
  border: 1px solid var(--line); z-index: 60; padding: 10px;
  box-shadow: 0 8px 30px rgba(0,0,0,.5);
}
.alerts h4 { color: var(--amber); font-size: 12px; margin-bottom: 8px; }
.alert-item { font-size: 12px; padding: 6px 8px; border-left: 2px solid var(--amber); background: rgba(255,181,69,.06); margin-bottom: 6px; }
.alert-item small { display: block; color: var(--txt-faint); font-size: 10px; margin-top: 2px; }
.alert-item.err { border-left-color: var(--red); background: rgba(255,77,106,.06); }

/* ---------- KPI 卡片：五卡一排，minmax(0,1fr) 允许收缩，字号随视口缩放 ---------- */
.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.kpi { padding: 12px 16px; position: relative; overflow: hidden; min-width: 0; }
.kpi .num { font-family: var(--font-num); font-size: clamp(20px, 1.9vw, 34px); font-weight: 700; color: var(--txt); line-height: 1; white-space: nowrap; }
.kpi .num small { font-size: clamp(10px, 0.75vw, 12px); color: var(--txt-dim); font-weight: 400; margin-left: 4px; }
.kpi .lab { color: var(--txt-dim); font-size: 11px; margin-top: 6px; letter-spacing: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* delta 改静态行内（不再绝对定位）——内容长度可变，绝对定位必撞大数字 */
.delta { display: block; text-align: right; font-size: 11px; font-family: var(--mono); margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.delta.up { color: var(--green); }
.delta.down { color: var(--red); }

/* ---------- 面板与角框 ---------- */
.panel {
  background: var(--panel); border: 1px solid var(--line); backdrop-filter: blur(6px);
  padding: 12px 14px; position: relative;
}
.p-title {
  color: var(--txt); font-size: 13px; letter-spacing: 1px; margin-bottom: 10px;
  display: flex; align-items: center; gap: 8px;
}
.p-title::before { content: ''; width: 3px; height: 12px; background: var(--cyan); box-shadow: 0 0 6px var(--cyan); }
.corner::before, .corner::after {
  content: ''; position: absolute; width: 14px; height: 14px; pointer-events: none;
}
.corner::before { top: -1px; left: -1px; border-top: 2px solid var(--cyan); border-left: 2px solid var(--cyan); }
.corner::after { bottom: -1px; right: -1px; border-bottom: 2px solid var(--cyan); border-right: 2px solid var(--cyan); }

/* ---------- 图表区 ---------- */
.grid-a { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 12px; }  /* 水位 1/3 宽，与图区B三等列同宽 */
/* 水位仪表：ECharts 只画环；数值图例放在图下方（普通文档流，永不重叠） */
.gauge-wrap { position: relative; }
.gauge-legend {
  display: flex; justify-content: center; gap: 26px; padding: 2px 0 10px;
  font-size: 11px; color: var(--txt-dim); letter-spacing: 1px;
}
.gl-item { display: inline-flex; align-items: center; gap: 6px; }
.gl-item b { color: var(--txt); font-size: 13px; font-family: var(--font-num); }
.gl-item .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.chart-box { height: 180px; }
.chart-box.tall { height: 220px; }
.grid-b { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.donut-wrap { position: relative; height: 190px; }
.donut-center {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  text-align: center; pointer-events: none;
}
.donut-center b { font-family: var(--font-num); font-size: 30px; color: var(--cyan); display: block; }
.donut-center span { font-size: 11px; color: var(--txt-dim); }

/* ---------- 节点卡片：1 个拉满，2/3 个平分，最多 4 个一排 ---------- */
.nodes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.nodes:has(> :nth-child(1):last-child) { grid-template-columns: 1fr; }
.nodes:has(> :nth-child(2):last-child) { grid-template-columns: repeat(2, 1fr); }
.nodes:has(> :nth-child(3):last-child) { grid-template-columns: repeat(3, 1fr); }
.node { cursor: pointer; transition: transform .15s, box-shadow .15s; }
.node:hover { transform: translateY(-2px); box-shadow: 0 6px 24px rgba(0, 200, 255, .12); }
.nt { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.nt .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.nt .dot.a { background: var(--green); box-shadow: 0 0 8px var(--green); animation: blink 2s infinite; }
.nt .dot.s { background: var(--amber); box-shadow: 0 0 6px var(--amber); }
.nt .gpu { margin-left: auto; font-size: 10px; color: var(--txt-dim); border: 1px solid var(--line); padding: 1px 6px; }
.nf { font-size: 11px; color: var(--txt-dim); margin-bottom: 6px; }
.nm { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; min-height: 20px; }
.nm .tag { font-size: 10px; padding: 1px 6px; border: 1px solid var(--cyan-dim); color: var(--cyan); }
.nm .tag.g { border-color: rgba(45,255,168,.4); color: var(--green); }
.nl { display: flex; gap: 12px; font-size: 11px; color: var(--txt-dim); margin-bottom: 8px; }
.nl .v { font-family: var(--font-num); font-size: 15px; color: var(--txt); }
.nl .v.warm { color: var(--amber); }
.nl .v.hot { color: var(--red); }
.nbar { display: flex; flex-direction: column; gap: 4px; }
.bar-row { display: flex; align-items: center; gap: 6px; font-size: 10px; color: var(--txt-faint); }
.bar { flex: 1; height: 4px; background: rgba(255,255,255,.06); position: relative; }
.bar i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--cyan); box-shadow: 0 0 6px rgba(0,229,255,.6); }
.bar.hot i { background: var(--red); box-shadow: 0 0 6px rgba(255,77,106,.6); }
.n-foot { display: flex; justify-content: space-between; font-size: 10px; color: var(--txt-faint); margin-top: 8px; border-top: 1px dashed rgba(0,229,255,.15); padding-top: 6px; }
.n-foot b { color: var(--txt-dim); font-family: var(--font-num); }
.node-detail { margin-top: 8px; }
.grid-c { display: grid; grid-template-columns: 1fr; gap: 12px; }  /* 事件流拉满整行 */

/* ---------- 布局：主区 + 右侧事件流独占列 ---------- */
.dash-wrap { display: flex; gap: 12px; align-items: stretch; }  /* 右列与主区同高（底部拉齐） */
.dash-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.dash-main > * { margin: 0; }
.dash-side { flex: 0 0 360px; width: 360px; max-width: 360px; display: flex; }
/* 事件列：主区短时随主区拉齐；主区超长时吸附视口顶、高度封顶（不超过分辨率） */
.ev-col { flex: 1; min-height: 400px; max-height: calc(100vh - 110px);
  display: flex; flex-direction: column; position: sticky; top: 12px; align-self: flex-start; }
.ev-col .ev-box { flex: 1; min-height: 0; height: auto; overflow: auto; }  /* 纵横双向滚动：长日志横向划 */
.ev-col .ev-line .m { overflow: visible; text-overflow: clip; }  /* 不截断，靠横滚看全 */

/* ---------- 集群事件流 ---------- */
.evts { max-height: 230px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.evt { display: flex; gap: 8px; font-size: 11px; font-family: var(--mono); align-items: baseline; }
.evt .t { color: var(--txt-faint); flex: none; }
.evt .l { flex: none; width: 44px; }
.evt .l.INFO { color: var(--cyan); }
.evt .l.OK { color: var(--green); }
.evt .l.WARN { color: var(--amber); }
.evt .l.ERROR { color: var(--red); }
.evts .empty { color: var(--txt-faint); font-size: 11px; }

/* ---------- 启动服务对话框样式已随入口迁往 Nodes.vue ---------- */

.svc table { width: 100%; border-collapse: collapse; font-size: 12px; }
.svc th {
  text-align: left; color: var(--txt-dim); font-weight: 600; font-size: 11px;
  padding: 6px 10px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
.svc td { padding: 7px 10px; border-bottom: 1px solid rgba(20,40,60,.4); white-space: nowrap; }
.svc tr:hover td { background: rgba(0,200,255,.04); }
.svc-dim { color: var(--txt-faint); font-size: 11px; }
.st { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; }
.st i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.st.a { color: var(--green); } .st.a i { background: var(--green); box-shadow: 0 0 6px var(--green); animation: blink 2s infinite; }
.st.s { color: var(--txt-faint); } .st.s i { background: var(--txt-faint); }
.st.e { color: var(--red); } .st.e i { background: var(--red); }
.op {
  background: transparent; border: 1px solid var(--line); color: var(--txt-dim);
  font-size: 11px; padding: 2px 10px; cursor: pointer; margin-right: 6px;
}
.op:hover { color: var(--cyan); border-color: var(--cyan-dim); }
.op.stop:hover { color: var(--red); border-color: rgba(255,77,90,.4); }
.empty { text-align: center; color: var(--txt-faint); padding: 20px !important; }

/* ---------- 节点详情抽屉 ---------- */
.mask {
  position: fixed; inset: 0; background: rgba(2, 6, 12, .6); z-index: 90;
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
.mask.on { opacity: 1; pointer-events: auto; }
.drawer {
  position: fixed; top: 0; right: -420px; width: 400px; height: 100vh; z-index: 100;
  background: var(--panel-solid); border-left: 1px solid var(--line);
  transition: right .25s ease; padding: 18px; overflow-y: auto;
  box-shadow: -10px 0 40px rgba(0,0,0,.5);
}
.drawer.open { right: 0; }
.d-close {
  position: absolute; top: 12px; right: 12px; background: none; border: 1px solid var(--line);
  color: var(--txt-dim); width: 26px; height: 26px; cursor: pointer;
}
.d-close:hover { color: var(--red); border-color: var(--red); }
.drawer h3 { font-family: var(--font-num); color: var(--cyan); letter-spacing: 2px; margin-bottom: 2px; }
.drawer .sub { font-size: 11px; color: var(--txt-dim); margin-bottom: 12px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.drawer .sub .tag { font-size: 10px; border: 1px solid var(--cyan-dim); color: var(--cyan); padding: 1px 6px; }
.tag.g { border-color: rgba(45,255,168,.4) !important; color: var(--green) !important; }
.tag.b { border-color: rgba(138,123,255,.4) !important; color: var(--violet) !important; }
.d-kpis { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.d-kpis .panel { padding: 8px 10px; }
.d-kpis .num { font-family: var(--font-num); font-size: 22px; }
.d-sec { margin-bottom: 14px; }
.d-sec p { font-size: 11px; color: var(--txt-dim); margin-bottom: 6px; letter-spacing: 1px; }
</style>
