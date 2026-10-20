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
        <button class="btn diag-btn" @click="diag" title="采集终端渲染状态，发我排查">诊断</button>
        <span class="hint">高危命令策略拦截 · 会话全程录屏审计</span>
      </div>
      <div ref="termEl" class="term-body" v-show="!mirrorMode"></div>
      <!-- 镜像模式：渲染器停摆时的纯文本兜底（数据真实到达但 xterm 一行未渲染时自动切换） -->
      <div v-if="mirrorMode" class="mirror-body">
        <div class="mirror-tip">⚠ 检测到渲染受阻（窗口被遮挡或渲染器停滞），已切换纯文本模式；窗口恢复可见后自动切回</div>
        <pre class="mirror-pre">{{ mirrorText }}</pre>
        <input class="mirror-inp" placeholder="在此输入命令，回车执行（兜底通道）"
               @keydown.enter.prevent="mirrorSend" />
      </div>
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
const mirrorMode = ref(false)   // 兜底镜像模式：渲染器停摆时显示纯文本
const mirrorText = ref('')
const termEl = ref<HTMLDivElement>()
let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null
let ro: ResizeObserver | null = null
let rafAliveAt = 0              // 最近一次 rAF 触发时刻（0 = 从未触发）
let lastDataAt = 0              // 最近一次收到 out 数据的时刻
let rawTail = ''                // 原始数据尾部（镜像模式用，去 ANSI）
let ptyCols = 0, ptyRows = 0    // 最后一次同步给后端的 PTY 尺寸

const ANSI_RE = /[](?:\[[0-9;?]*[A-Za-z]|\][^]*|\([0-9A-Z]|[=>#][0-9]?|[A-Z@-_])/g
function stripAnsi(s: string) { return s.replace(ANSI_RE, '') }

function trackRaf() {
  const tick = () => { rafAliveAt = performance.now(); requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
}

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
  startWatchdog()

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
  ptyCols = term!.cols; ptyRows = term!.rows
  ws = new WebSocket(`${proto}://${location.host}/api/term/ws?token=${encodeURIComponent(token)}` +
    `&node_id=${encodeURIComponent(nodeId.value)}&cols=${ptyCols}&rows=${ptyRows}`)
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'out') {
      term!.write(msg.data)
      term!.scrollToBottom()
      lastDataAt = performance.now()
      rawTail = (rawTail + stripAnsi(msg.data)).slice(-60000)
    } else if (msg.type === 'blocked') {
      term!.write(`\x1b[31m${msg.msg}\x1b[0m`)
      rawTail = (rawTail + '\n⛔ ' + msg.msg + '\n').slice(-60000)
    } else if (msg.type === 'err') { term!.write(`\x1b[31m[error] ${msg.msg}\x1b[0m\r\n`); connected.value = false }
    else if (msg.type === 'ready') { connected.value = true; recName.value = msg.rec || '' }
  }
  ws.onclose = () => { connected.value = false }
  term!.focus()
}

function disconnect() { ws?.close(); connected.value = false }

// 镜像模式输入：回车发送（与原 'in' 通道一致）
function mirrorSend(e: Event) {
  const inp = e.target as HTMLInputElement
  const cmd = inp.value
  if (ws?.readyState === WebSocket.OPEN && cmd) {
    ws.send(JSON.stringify({ type: 'in', data: cmd + '\r' }))
    inp.value = ''
  }
}

// 终端自诊断：一键采集渲染管线状态（复制到剪贴板，排障用）
async function diag() {
  const rows = term?.element?.querySelectorAll('.xterm-rows > div')
  const nonEmpty = rows ? Array.from(rows).filter(r => (r.textContent || '').trim()).length : 0
  const rafPerHalfSec = await new Promise<number>(res => {
    let n = 0
    const t0 = performance.now()
    const tick = () => { n++; if (performance.now() - t0 < 500) requestAnimationFrame(tick); else res(n) }
    requestAnimationFrame(tick)
    setTimeout(() => res(n), 1200)
  })
  const xt = term?.element
  const info = {
    ua: navigator.userAgent,
    可见性: document.visibilityState,
    聚焦: document.hasFocus(),
    rAF每半秒: rafPerHalfSec,
    字体状态: (document as Document).fonts?.status,
    xterm实例: !!term,
    渲染器类名: xt?.className || '',
    xterm行列: term ? `${term.cols}x${term.rows}` : '',
    PTY已同步: `${ptyCols}x${ptyRows}`,
    渲染行数: rows?.length ?? 0,
    非空行: nonEmpty,
    镜像模式: mirrorMode.value,
    WS状态: ws ? ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState] : 'null',
    已连接: connected.value,
    数据尾部: rawTail.slice(-120),
    错误条: (document.getElementById('hs-fatal') || { textContent: '' }).textContent
  }
  const text = JSON.stringify(info, null, 1)
  try { await navigator.clipboard.writeText(text) } catch { /* 剪贴板不可用就只看 */ }
  alert('诊断信息已复制到剪贴板，请粘贴发我：\n\n' + text)
}

/* ---------- 渲染看门狗 ----------
   覆盖三类停摆：
   a) rAF 停滞（窗口被完全遮挡/嵌入视图 hidden）→ 调内部 API 同步重绘
   b) 输入框内容滞留（xterm 未消费）→ 补发 input 事件
   c) PTY 尺寸与显示漂移 → 重发 resize
   连续 2s 有数据但渲染全空 → 切纯文本镜像模式（保底可见可输入） */
function startWatchdog() {
  trackRaf()
  window.setInterval(() => {
    if (!term) return
    const now = performance.now()
    const rows = term.element?.querySelectorAll('.xterm-rows > div')
    const nonEmpty = rows ? Array.from(rows).filter(r => (r.textContent || '').trim()).length : 0
    const rafStalled = rafAliveAt === 0 || now - rafAliveAt > 1500

    // a) rAF 停滞 → 绕过 rAF 调度直接渲染
    if (rafStalled && now - lastDataAt < 5000) {
      try {
        const rs: any = (term as any)._core?._renderService
        ;(rs?._renderer?.renderRows ?? rs?.renderRows)?.call(rs?._renderer ?? rs, 0, term.rows - 1)
      } catch { /* 内部 API 不存在就放弃，镜像模式兜底 */ }
    }
    // b) 输入滞留 → 补发 input 事件让 xterm 消费
    const ta = term.element?.querySelector('.xterm-helper-textarea') as HTMLTextAreaElement | null
    if (ta && ta.value) ta.dispatchEvent(new Event('input', { bubbles: true }))
    // c) 几何漂移 → 重发 resize
    if (ws?.readyState === WebSocket.OPEN && (term.cols !== ptyCols || term.rows !== ptyRows)) {
      ptyCols = term.cols; ptyRows = term.rows
      ws.send(JSON.stringify({ type: 'resize', cols: ptyCols, rows: ptyRows }))
    }
    // 镜像模式判定
    const stalled = connected.value && (now - lastDataAt < 2000 || rawTail.length > 0) && nonEmpty === 0 && rafStalled
    if (stalled && !mirrorMode.value) { mirrorMode.value = true; mirrorText.value = rawTail }
    if (mirrorMode.value) {
      mirrorText.value = rawTail
      if (nonEmpty > 2 && !rafStalled) mirrorMode.value = false  // 渲染恢复 → 退回 xterm
    }
  }, 500)
}

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
.diag-btn { padding: 4px 10px; font-size: 11px; }
.hint { margin-left: auto; font-size: 11px; }
.term-body { flex: 1; padding: 8px 4px 8px 12px; overflow: hidden; }
.term-body :deep(.xterm) { height: 100%; }
.mirror-body { flex: 1; display: flex; flex-direction: column; padding: 8px 12px; overflow: hidden; }
.mirror-tip { color: var(--yellow, #ffc857); font-size: 11px; margin-bottom: 6px; }
.mirror-pre {
  flex: 1; overflow: auto; background: #050b12; border: 1px solid var(--line);
  padding: 10px; font: 13px/1.45 'Cascadia Mono', Consolas, monospace; color: #c8d6e5;
  white-space: pre-wrap; word-break: break-all; margin: 0;
}
.mirror-inp {
  margin-top: 8px; background: rgba(0, 30, 50, .6); border: 1px solid var(--cyan-dim);
  color: var(--cyan); padding: 8px 12px; font: 13px 'Cascadia Mono', Consolas, monospace; outline: none;
}
</style>
