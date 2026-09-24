<template>
  <div class="hs-page">
    <header class="topbar">
      <div class="brand">衡枢 <span>HENGSHU</span><em>万流归一 · 衡以分之</em></div>
      <div>
        <el-tag :type="clusterOk ? 'success' : 'danger'" effect="dark">
          {{ clusterOk ? '集群正常' : '存在离线节点' }}
        </el-tag>
        <el-button size="small" text @click="logout">退出</el-button>
      </div>
    </header>

    <div class="hs-kpi-grid" v-if="summary">
      <div class="hs-panel"><div class="hs-kpi-label">在线节点</div>
        <div class="hs-kpi-value">{{ summary.nodes_online }}/{{ summary.nodes_total }}</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">运行服务</div>
        <div class="hs-kpi-value">{{ summary.services_running }}/{{ summary.services_total }}</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">活跃会话</div>
        <div class="hs-kpi-value">{{ summary.sessions }}</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">加权 GPU</div>
        <div class="hs-kpi-value">{{ summary.gpu_util_weighted }}%</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">加权显存</div>
        <div class="hs-kpi-value">{{ summary.vram_weighted }}%</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">平均 TTFT</div>
        <div class="hs-kpi-value">{{ summary.ttft_avg }}ms</div></div>
      <div class="hs-panel"><div class="hs-kpi-label">总吞吐 tok/s</div>
        <div class="hs-kpi-value">{{ summary.tokens_per_s_total }}</div></div>
    </div>

    <div class="hs-main-grid">
      <div>
        <div class="hs-panel" style="margin-bottom:14px">
          <div class="hs-kpi-label" style="margin-bottom:8px">API QPS</div>
          <div ref="qpsEl" style="height:220px"></div>
        </div>
        <div class="hs-panel">
          <div class="hs-kpi-label" style="margin-bottom:10px">模型服务</div>
          <el-table :data="services" size="small" empty-text="暂无实例（等待 Agent 心跳）">
            <el-table-column prop="model" label="模型" min-width="140" />
            <el-table-column prop="node" label="节点" min-width="110" />
            <el-table-column prop="framework" label="框架" width="90" />
            <el-table-column label="GPU" width="90">
              <template #default="{ row }">{{ row.metrics?.m_gpu_util ?? '-' }}%</template>
            </el-table-column>
            <el-table-column label="TTFT" width="90">
              <template #default="{ row }">{{ row.metrics?.m_ttft ?? '-' }}ms</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'running' ? 'success' : 'info'">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="danger" text
                           @click="stop(row.node, row.model)">停止</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <div>
        <div class="hs-panel" style="margin-bottom:14px">
          <div class="hs-kpi-label" style="margin-bottom:10px">节点</div>
          <div v-for="n in nodes" :key="n.node_id" class="hs-node">
            <span><i class="hs-dot" :class="n.status"></i>{{ n.node_id }}</span>
            <span style="color:#8a94a8;font-size:12px">{{ n.arch }} · {{ n.instances }} 实例</span>
          </div>
          <div v-if="!nodes.length" style="color:#556;font-size:12px">等待节点心跳…</div>
        </div>
        <div class="hs-panel">
          <div class="hs-kpi-label" style="margin-bottom:10px">集群事件流</div>
          <div v-for="(e, i) in events" :key="i" class="hs-ev" :class="e.level">
            [{{ e.time }}] {{ e.level }} {{ e.text }}
          </div>
          <div v-if="!events.length" style="color:#556;font-size:12px">暂无事件</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import * as echarts from 'echarts'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'

const summary = ref<any>(null)
const nodes = ref<any[]>([])
const services = ref<any[]>([])
const qpsEl = shallowRef<HTMLElement>()
let chart: echarts.ECharts | null = null
let timer = 0
const router = useRouter()
const auth = useAuthStore()

const events = computed(() => (summary.value?.events || []).slice().reverse())
const clusterOk = computed(() =>
  summary.value ? summary.value.nodes_online === summary.value.nodes_total : true)

async function refresh() {
  const [s, n, sv, q] = await Promise.all([
    api.summary(), api.nodes(), api.services(), api.qpsSeries()
  ])
  summary.value = s
  nodes.value = n.nodes
  services.value = sv.services
  if (!chart && qpsEl.value) chart = echarts.init(qpsEl.value)
  chart?.setOption({
    grid: { left: 40, right: 12, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', show: false, data: q.series.map((_: number, i: number) => i) },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(0,229,255,0.08)' } },
             axisLabel: { color: '#8a94a8' } },
    series: [{ type: 'line', data: q.series, smooth: true, showSymbol: false,
               lineStyle: { color: '#00e5ff' },
               areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
                 [{ offset: 0, color: 'rgba(0,229,255,0.35)' },
                  { offset: 1, color: 'rgba(0,229,255,0.02)' }]) } }]
  })
}

async function stop(node: string, name: string) {
  await api.stopService(node, name)
  await refresh()
}

function logout() {
  auth.logout()
  router.push('/login')
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 3000)
})
onUnmounted(() => { clearInterval(timer); chart?.dispose() })
</script>

<style scoped>
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.brand { color: #00e5ff; font-size: 22px; letter-spacing: 5px; font-weight: 700; }
.brand span { font-size: 12px; color: #8a94a8; letter-spacing: 3px; margin-left: 6px; }
.brand em { font-style: normal; font-size: 11px; color: #556; margin-left: 14px; letter-spacing: 2px; }
</style>
