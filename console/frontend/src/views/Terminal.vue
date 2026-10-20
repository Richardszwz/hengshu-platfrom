<template>
  <AppLayout crumb-prefix="HENGSHU" crumb-title="WEB 终端">
    <div class="multi-bar">
      <button class="btn btn-cyan" :disabled="store.sessions.length >= 4" @click="add">＋ 添加终端</button>
      <span class="count">{{ store.sessions.length }} / 4</span>
      <span class="hint">高危命令策略拦截 · 会话全程录屏审计 · 会话跨页面保持</span>
    </div>
    <div class="terms-grid" :class="'n' + store.sessions.length">
      <div v-for="s in store.sessions" :key="s.id" class="pane panel">
        <div class="pane-bar">
          <select v-model="s.nodeId" class="sel" :disabled="s.connected">
            <option v-for="n in store.nodes" :key="n.node_id" :value="n.node_id">
              {{ n.node_id }}（{{ n.hostname || 'unknown' }} · {{ n.arch }}）
            </option>
          </select>
          <button v-if="!s.connected" class="btn btn-cyan" :disabled="!s.nodeId" @click="connect(s)">连接</button>
          <button v-else class="btn" @click="disconnect(s)">断开</button>
          <span class="chip" :class="s.connected ? 'ok' : 'idle'">{{ s.connected ? 'LIVE' : 'IDLE' }}</span>
          <span v-if="s.recName" class="rec" :title="s.recName">⏺</span>
          <button class="x" :disabled="store.sessions.length <= 1" title="关闭终端" @click="remove(s)">×</button>
        </div>
        <div class="pane-body" :ref="el => attach(s, el)"></div>
        <!-- 镜像模式：渲染停摆时的纯文本兜底 -->
        <div v-if="s.mirrorMode" class="mirror-body">
          <div class="mirror-tip">⚠ 渲染受阻，已切纯文本模式；恢复可见后自动切回</div>
          <pre class="mirror-pre">{{ s.mirrorText }}</pre>
          <input class="mirror-inp" placeholder="在此输入命令，回车执行（兜底通道）"
                 @keydown.enter.prevent="mirrorEnter(s, $event)" />
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { nextTick, onMounted } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import '@xterm/xterm/css/xterm.css'
import {
  store, initManager, addSession, removeSession,
  connect, disconnect, mirrorSend, syncResize,
  type TermSession
} from '../term/manager'

onMounted(() => { initManager() })

// ref 回调：pane-body 挂载时把 session 的常驻 host 挂进来（路由回来也走这里）
function attach(s: TermSession, el: Element | null) {
  if (!el) return
  if (s.host.parentElement !== el) el.appendChild(s.host)
  nextTick(() => {
    try {
      s.fit.fit()
      syncResize(s)
    } catch { /* 忽略 */ }
  })
}

function add() { addSession(false) }  // 新增终端不自动连接，用户自己点连接
function remove(s: TermSession) { removeSession(s.id) }

function mirrorEnter(s: TermSession, e: Event) {
  const inp = e.target as HTMLInputElement
  mirrorSend(s, inp.value)
  inp.value = ''
}
</script>

<style scoped>
.multi-bar {
  display: flex; align-items: center; gap: 10px; padding: 0 0 8px;
  font-size: 12px; color: var(--txt-dim);
}
.count { font-size: 11px; }
.hint { margin-left: auto; font-size: 11px; }
.terms-grid {
  display: grid; gap: 8px; height: calc(100vh - 158px);
  grid-template-columns: 1fr; grid-template-rows: 1fr;
}
.terms-grid.n2 { grid-template-columns: 1fr 1fr; }
/* 3 路品字型：上排两个，下排一个通栏 */
.terms-grid.n3 { grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; }
.terms-grid.n3 .pane:nth-child(3) { grid-column: 1 / -1; }
.terms-grid.n4 { grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; }

.pane { display: flex; flex-direction: column; overflow: hidden; min-height: 0; min-width: 0; }
.pane-bar {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border-bottom: 1px solid var(--line); font-size: 12px; color: var(--txt-dim); flex: 0 0 auto;
}
.sel {
  background: rgba(0, 30, 50, .6); color: var(--txt); border: 1px solid var(--line);
  padding: 4px 8px; font-size: 11px; min-width: 180px; outline: none;
}
.btn {
  background: rgba(0, 200, 255, .08); border: 1px solid var(--cyan-dim); color: var(--cyan);
  padding: 4px 12px; font-size: 11px; cursor: pointer;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
}
.btn:disabled { opacity: .4; cursor: not-allowed; }
.chip { padding: 2px 8px; font-size: 10px; border: 1px solid var(--line); }
.chip.ok { color: var(--green); border-color: rgba(61,255,160,.4); }
.chip.idle { color: var(--txt-dim); }
.rec { color: var(--red); font-size: 11px; }
.x {
  margin-left: auto; background: none; border: 1px solid var(--line); color: var(--txt-dim);
  width: 22px; height: 22px; cursor: pointer; font-size: 13px; line-height: 1;
}
.x:hover:not(:disabled) { color: var(--red); border-color: var(--red); }
.x:disabled { opacity: .3; cursor: not-allowed; }
.pane-body { flex: 1; padding: 4px 2px 4px 8px; overflow: hidden; min-height: 0; }
.pane-body :deep(.term-host) { height: 100%; }
.pane-body :deep(.xterm) { height: 100%; }
.mirror-body { flex: 1; display: flex; flex-direction: column; padding: 8px 10px; overflow: hidden; }
.mirror-tip { color: var(--yellow, #ffc857); font-size: 11px; margin-bottom: 4px; }
.mirror-pre {
  flex: 1; overflow: auto; background: #050b12; border: 1px solid var(--line);
  padding: 8px; font: 12px/1.45 'Cascadia Mono', Consolas, monospace; color: #c8d6e5;
  white-space: pre-wrap; word-break: break-all; margin: 0;
}
.mirror-inp {
  margin-top: 6px; background: rgba(0, 30, 50, .6); border: 1px solid var(--cyan-dim);
  color: var(--cyan); padding: 6px 10px; font: 12px 'Cascadia Mono', Consolas, monospace; outline: none;
}
</style>
