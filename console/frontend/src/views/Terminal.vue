<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="WEB 终端">
    <div class="panel term-panel">
      <div class="term-bar">
        <label>目标节点</label>
        <select v-model="nodeId" class="sel" :disabled="connected">
          <option v-for="n in onlineNodes" :key="n.node_id" :value="n.node_id">
            {{ n.node_id }}（{{ n.hostname || 'unknown' }} · {{ n.arch }}）
          </option>
        </select>
        <button v-if="!connected" class="btn btn-cyan" :disabled="!nodeId" @click="connect">连接</button>
        <button v-else class="btn" @click="disconnect">断开</button>
        <span class="chip" :class="connected ? 'ok' : 'idle'">{{ connected ? 'SESSION LIVE' : 'IDLE' }}</span>
        <span v-if="recName" class="rec">⏺ {{ recName }}</span>
        <span class="hint">高危命令策略拦截 · 会话全程录屏审计</span>
      </div>
      <div ref="termEl" class="term-body"></div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import AppLayout from '../components/AppLayout.vue'
import { api } from '../api/client'

const nodeId = ref('')
const onlineNodes = ref<any[]>([])
const connected = ref(false)
const recName = ref('')
const termEl = ref<HTMLDivElement>()
let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let ro: ResizeObserver | null = null

api.nodes().then(d => {
  // Web 终端走 SSH（已存凭证），不要求 Agent 心跳在线；pending 节点也可连
  onlineNodes.value = d.nodes || []
  if (onlineNodes.value.length) nodeId.value = onlineNodes.value[0].node_id
})

function ensureTerm() {
  if (term) return
  term = new Terminal({
    fontFamily: 'Cascadia Mono, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.0,
    letterSpacing: 0,
    cursorBlink: true,
    theme: {
      background: '#050b12', foreground: '#c8d6e5', cursor: '#00c8ff',
      selectionBackground: 'rgba(0,200,255,.25)',
      black: '#1b2a3a', red: '#ff4d5a', green: '#3dffa0', yellow: '#ffc857',
      blue: '#00c8ff', magenta: '#b58cff', cyan: '#00e5c8', white: '#e8f1f8'
    }
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(termEl.value!)
  fit.fit()
  term.onData(d => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: 'in', data: d })))
  ro = new ResizeObserver(() => {
    fit?.fit()
    if (ws?.readyState === WebSocket.OPEN && term && fit) {
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    }
  })
  ro.observe(termEl.value!)

  // canvas 渲染器依赖字体实测：字体未就绪时 fit 的字符格尺寸是错的
  // → 光标错位/文字画出画布外（"能输入但看不见"）。字体就绪后强制重测+重绘。
  const refitAndSync = () => {
    if (!term || !fit) return
    fit.fit()
    // 清 glyph 缓存按新字体重绘
    term.clearTextureAtlas()
    term.refresh(0, term.rows - 1)
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    }
  }
  // fonts.ready 可能因离线 CDN 字体挂起——800ms 兜底，先到先执行
  Promise.race([
    (document as Document).fonts?.ready ?? Promise.resolve(),
    new Promise(r => setTimeout(r, 800))
  ]).then(refitAndSync)
  // 保险：页面全部资源就绪后再校正一次（二次打开也安全，term 已存在则 ensureTerm 直接返回）
  if (document.readyState !== 'complete') {
    window.addEventListener('load', refitAndSync, { once: true })
  }
}

function connect() {
  ensureTerm()
  const token = localStorage.getItem('hs_token') || ''
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/term/ws?token=${encodeURIComponent(token)}` +
    `&node_id=${encodeURIComponent(nodeId.value)}&cols=${term!.cols}&rows=${term!.rows}`)
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'out') term!.write(msg.data)
    else if (msg.type === 'blocked') term!.write(`\x1b[31m${msg.msg}\x1b[0m`)
    else if (msg.type === 'err') { term!.write(`\x1b[31m[error] ${msg.msg}\x1b[0m\r\n`); connected.value = false }
    else if (msg.type === 'ready') { connected.value = true; recName.value = msg.rec || '' }
  }
  ws.onclose = () => { connected.value = false }
  term!.focus()
}

function disconnect() { ws?.close(); connected.value = false }

onUnmounted(() => { disconnect(); ro?.disconnect(); term?.dispose() })
</script>

<style scoped>
.term-panel { display: flex; flex-direction: column; height: calc(100vh - 120px); }
.term-bar {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border-bottom: 1px solid var(--line); font-size: 12px; color: var(--txt-dim);
}
.sel {
  background: rgba(0, 30, 50, .6); color: var(--txt); border: 1px solid var(--line);
  padding: 6px 10px; font-size: 12px; min-width: 260px; outline: none;
}
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 6px 16px; font-size: 12px; cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.btn:disabled { opacity: .4; cursor: not-allowed; }
.chip { padding: 3px 10px; font-size: 11px; border: 1px solid var(--line); }
.chip.ok { color: var(--green); border-color: rgba(61,255,160,.4); }
.chip.idle { color: var(--txt-dim); }
.rec { color: var(--red); font-size: 11px; }
.hint { margin-left: auto; font-size: 11px; }
.term-body { flex: 1; padding: 8px 4px 8px 12px; overflow: hidden; }
.term-body :deep(.xterm) { height: 100%; }
</style>
