<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="审计日志">
    <div class="page-header">
      <div class="page-title">审计日志</div>
      <div class="page-sub">终端会话审计 · 高危命令拦截 · 安装事件 · 录屏回放</div>
    </div>

    <div class="panel">
      <div class="panel-title">
        <span>事件流</span>
        <span class="filters">
          <button v-for="f in filters" :key="f" class="filter-btn"
                  :class="{ active: filter === f }" @click="filter = f; load()">
            {{ f }}
          </button>
        </span>
        <button class="refresh" @click="load">刷新</button>
      </div>
      <div v-if="filtered.length === 0" class="empty">暂无事件</div>
      <div v-else class="event-list">
        <div v-for="(e, i) in filtered" :key="i" class="event-row" :class="'lv-' + e.level">
          <span class="ev-ts">{{ fmt(e.ts) }}</span>
          <span class="ev-lv">{{ e.level }}</span>
          <span class="ev-msg">{{ e.msg }}</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title"><span>终端录屏</span><button class="refresh" @click="loadRecs">刷新</button></div>
      <div v-if="recs.length === 0" class="empty">暂无录屏</div>
      <table v-else class="tbl">
        <thead>
          <tr><th>录屏文件</th><th>大小</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in recs" :key="r.name">
            <td class="mono">{{ r.name }}</td>
            <td>{{ r.size }} B</td>
            <td><a class="dl-link" :href="'/api/term/recordings/' + encodeURIComponent(r.name)"
                   target="_blank">下载 .cast</a></td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'

const events = ref<any[]>([])
const recs = ref<any[]>([])
const filter = ref('全部')
const filters = ['全部', 'AUDIT', 'WARN', 'ERR', 'OK']

const filtered = computed(() =>
  filter.value === '全部' ? events.value : events.value.filter((e) => e.level === filter.value))

function fmt(ts: number) {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function load() {
  try {
    events.value = (await api.auditsEvents()).events ?? []
  } catch { /* 401 拦截器统一处理 */ }
}
async function loadRecs() {
  try {
    recs.value = (await api.recordings()).recordings ?? []
  } catch { /* 同上 */ }
}
onMounted(() => { load(); loadRecs() })
</script>

<style scoped>
.page-header { margin-bottom: 16px; }
.page-title { font-family: var(--font-display, 'Chakra Petch'); font-size: 22px; font-weight: 700; letter-spacing: 0.08em; }
.page-sub { color: var(--muted-foreground, #8b98b8); font-size: 12px; margin-top: 4px; }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; overflow: hidden; }
.panel-title { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 13px; }
.filters { display: flex; gap: 6px; }
.filter-btn { background: transparent; border: 1px solid var(--border); color: inherit; border-radius: 4px; padding: 2px 10px; font-size: 12px; cursor: pointer; }
.filter-btn.active { border-color: var(--accent, #37b6ff); color: var(--accent, #37b6ff); }
.refresh { margin-left: auto; background: transparent; border: 1px solid var(--border); color: inherit; border-radius: 4px; padding: 2px 12px; font-size: 12px; cursor: pointer; }
.empty { padding: 24px; text-align: center; color: var(--muted-foreground, #8b98b8); font-size: 12px; }
.event-list { max-height: 420px; overflow-y: auto; font-size: 12px; }
.event-row { display: flex; gap: 12px; padding: 6px 16px; border-bottom: 1px solid var(--border); }
.ev-ts { color: var(--muted-foreground, #8b98b8); white-space: nowrap; font-family: var(--font-mono, monospace); }
.ev-lv { width: 48px; text-align: center; border-radius: 3px; height: fit-content; }
.lv-ERR .ev-lv, .lv-WARN .ev-lv { color: #ff5f6b; }
.lv-OK .ev-lv { color: #2ee6a8; }
.lv-AUDIT .ev-lv { color: #37b6ff; }
.ev-msg { flex: 1; word-break: break-all; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th { text-align: left; padding: 8px 16px; color: var(--muted-foreground, #8b98b8); border-bottom: 1px solid var(--border); font-weight: 500; }
.tbl td { padding: 8px 16px; border-bottom: 1px solid var(--border); }
.mono { font-family: var(--font-mono, monospace); }
.dl-link { color: var(--accent, #37b6ff); text-decoration: none; }
</style>
