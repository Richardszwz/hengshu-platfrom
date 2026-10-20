// 多终端会话管理器（模块级单例）：
// xterm 实例 + WS 常驻内存，路由切换只摘挂 DOM，连接不断、录屏不停、滚动历史不丢。
// 上限 4 路；2 路横排、3 路品字、4 路田字（布局在 Terminal.vue）。
import { markRaw, reactive } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { api } from '../api/client'

export interface TermSession {
  id: number
  nodeId: string
  connected: boolean
  recName: string
  mirrorMode: boolean
  mirrorText: string
  term: Terminal
  fit: FitAddon
  host: HTMLDivElement       // 承载 xterm 的 div，随 session 走（可摘挂）
  ws: WebSocket | null
  lastDataAt: number
  rawTail: string
  ptyCols: number
  ptyRows: number
}

const ANSI_RE = /\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07|\([0-9A-Z]|[=>#][0-9]?|[A-Z@-_])/g
function stripAnsi(s: string) { return s.replace(ANSI_RE, '') }

export const store = reactive({
  sessions: [] as TermSession[],
  nodes: [] as any[],
  nextId: 1,
})

function defaultNode(): string {
  return store.nodes[0]?.node_id || ''
}

function makeSession(): TermSession {
  const host = markRaw(document.createElement('div'))
  host.className = 'term-host'
  const term = markRaw(new Terminal({
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
  }))
  const fit = markRaw(new FitAddon())
  term.loadAddon(fit)
  term.open(host)

  const s: TermSession = reactive({
    id: store.nextId++,
    nodeId: defaultNode(),
    connected: false,
    recName: '',
    mirrorMode: false,
    mirrorText: '',
    term, fit, host,
    ws: null,
    lastDataAt: 0,
    rawTail: '',
    ptyCols: 0,
    ptyRows: 0,
  }) as TermSession

  term.onData(d => {
    if (s.ws?.readyState === WebSocket.OPEN)
      s.ws.send(JSON.stringify({ type: 'in', data: d }))
  })

  // 字体就绪后重测（canvas 渲染器字体度量竞态防护），兜底 800ms
  const refit = () => {
    try {
      s.fit.fit()
      s.term.clearTextureAtlas()
      s.term.refresh(0, s.term.rows - 1)
      syncResize(s)
    } catch { /* 元素未挂载时跳过 */ }
  }
  Promise.race([
    (document as Document).fonts?.ready ?? Promise.resolve(),
    new Promise(r => setTimeout(r, 800))
  ]).then(refit)

  return s
}

export async function initManager(): Promise<void> {
  if (!store.nodes.length) {
    try {
      const d = await api.nodes()
      store.nodes = d.nodes || []
    } catch { /* 401 拦截器处理 */ }
  }
  if (!store.sessions.length) addSession(false)
  startWatchdogOnce()
}

export function addSession(autoConnect = true): TermSession | null {
  if (store.sessions.length >= 4) return null
  const s = makeSession()
  store.sessions.push(s)
  if (autoConnect && s.nodeId) connect(s)
  return s
}

export function removeSession(id: number): void {
  const i = store.sessions.findIndex(x => x.id === id)
  if (i < 0) return
  const s = store.sessions[i]
  disconnect(s)
  try { s.term.dispose() } catch { /* 忽略 */ }
  s.host.remove()
  store.sessions.splice(i, 1)
}

export function connect(s: TermSession): void {
  if (s.ws && s.ws.readyState === WebSocket.OPEN) return
  const token = localStorage.getItem('hs_token') || ''
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  // 首次连接时 host 可能尚未挂进布局，fit 一次拿真实行列
  try { s.fit.fit() } catch { /* 忽略 */ }
  s.ptyCols = s.term.cols
  s.ptyRows = s.term.rows
  const ws = new WebSocket(
    `${proto}://${location.host}/api/term/ws?token=${encodeURIComponent(token)}` +
    `&node_id=${encodeURIComponent(s.nodeId)}&cols=${s.ptyCols}&rows=${s.ptyRows}`)
  s.ws = markRaw(ws)
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'out') {
      s.term.write(msg.data)
      s.term.scrollToBottom()
      s.lastDataAt = performance.now()
      s.rawTail = (s.rawTail + stripAnsi(msg.data)).slice(-60000)
    } else if (msg.type === 'blocked') {
      s.term.write(`\x1b[31m${msg.msg}\x1b[0m`)
      s.rawTail = (s.rawTail + '\n⛔ ' + msg.msg + '\n').slice(-60000)
    } else if (msg.type === 'err') {
      s.term.write(`\x1b[31m[error] ${msg.msg}\x1b[0m\r\n`)
      s.connected = false
    } else if (msg.type === 'ready') {
      s.connected = true
      s.recName = msg.rec || ''
    }
  }
  ws.onclose = () => { s.connected = false }
}

export function disconnect(s: TermSession): void {
  try { s.ws?.close() } catch { /* 忽略 */ }
  s.connected = false
}

export function syncResize(s: TermSession): void {
  if (s.ws?.readyState === WebSocket.OPEN &&
      (s.term.cols !== s.ptyCols || s.term.rows !== s.ptyRows)) {
    s.ptyCols = s.term.cols
    s.ptyRows = s.term.rows
    s.ws.send(JSON.stringify({ type: 'resize', cols: s.ptyCols, rows: s.ptyRows }))
  }
}

export function mirrorSend(s: TermSession, cmd: string): void {
  if (s.ws?.readyState === WebSocket.OPEN && cmd) {
    s.ws.send(JSON.stringify({ type: 'in', data: cmd + '\r' }))
  }
}

/* ---------- 渲染看门狗（全局一份，巡检全部会话） ---------- */
let rafAliveAt = 0
let watchdogStarted = false

function startWatchdogOnce(): void {
  if (watchdogStarted) return
  watchdogStarted = true
  const tick = () => { rafAliveAt = performance.now(); requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  window.setInterval(() => {
    const now = performance.now()
    const rafStalled = rafAliveAt === 0 || now - rafAliveAt > 1500
    for (const s of store.sessions) {
      // a) rAF 停滞 → 绕过 rAF 调度直接渲染
      if (rafStalled && now - s.lastDataAt < 5000) {
        try {
          const rs: any = (s.term as any)._core?._renderService
          ;(rs?._renderer?.renderRows ?? rs?.renderRows)?.call(rs?._renderer ?? rs, 0, s.term.rows - 1)
        } catch { /* 内部 API 不存在就跳过，镜像兜底 */ }
      }
      // b) 输入滞留 → 补发 input 事件
      const ta = s.host.querySelector('.xterm-helper-textarea') as HTMLTextAreaElement | null
      if (ta && ta.value) ta.dispatchEvent(new Event('input', { bubbles: true }))
      // c) 几何漂移 → 重发 resize
      syncResize(s)
      // d) 镜像模式判定：有数据但渲染全空且 rAF 停滞
      const rows = s.host.querySelectorAll('.xterm-rows > div')
      const nonEmpty = Array.from(rows).filter(r => (r.textContent || '').trim()).length
      const stalled = s.connected && (now - s.lastDataAt < 2000 || s.rawTail.length > 0) &&
                      nonEmpty === 0 && rafStalled
      if (stalled && !s.mirrorMode) { s.mirrorMode = true; s.mirrorText = s.rawTail }
      if (s.mirrorMode) {
        s.mirrorText = s.rawTail
        if (nonEmpty > 2 && !rafStalled) s.mirrorMode = false
      }
    }
  }, 500)
}
