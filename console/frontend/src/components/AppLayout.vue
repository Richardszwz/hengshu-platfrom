<template>
  <div class="app">
    <aside class="side">
      <div class="logo">
        <div class="logo-mark"></div>
        <div><b>衡枢<span style="color:var(--cyan);font-size:12px;letter-spacing:1px"> HENGSHU</span></b><small>GPU CLUSTER LB</small></div>
      </div>
      <nav class="nav">
        <router-link to="/" :class="{ on: route.name === 'dashboard' }"><span class="ico"></span>概览驾驶舱</router-link>
        <router-link to="/nodes" :class="{ on: route.name === 'nodes' }"><span class="ico"></span>节点管理</router-link>
        <router-link to="/terminal" :class="{ on: route.name === 'terminal' }"><span class="ico"></span>Web 终端</router-link>
        <router-link to="/tenants" :class="{ on: route.name === 'tenants' }"><span class="ico"></span>租户管理</router-link>
        <router-link to="/rules" :class="{ on: route.name === 'rules' }"><span class="ico"></span>规则路由</router-link>
        <router-link to="/audit" :class="{ on: route.name === 'audit' }"><span class="ico"></span>审计日志</router-link>
        <router-link to="/settings" :class="{ on: route.name === 'settings' }"><span class="ico"></span>系统设置</router-link>
      </nav>
      <div class="side-foot">
        <div class="avatar">R</div>
        <div><b>admin</b><small>平台管理员 · v0.1.0</small></div>
        <button class="logout-btn" @click="logout">退出</button>
      </div>
    </aside>

    <div class="main">
      <div class="topbar">
        <div class="sys-state" :class="{ warn: degraded }">
          <span class="blink"></span>{{ degraded ? 'NODE DEGRADED' : 'SYSTEM ONLINE' }}
        </div>
        <div class="crumb">{{ crumbPrefix }} / <b>{{ crumbTitle }}</b></div>
        <div class="top-right">
          <slot name="top-right"></slot>
          <div class="clock">{{ clock }}</div>
        </div>
      </div>
      <slot></slot>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

defineProps<{ crumbPrefix: string; crumbTitle: string; degraded?: boolean }>()

const clock = ref('--:--:--')
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
let clockTimer = 0

function tick() {
  clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}
function logout() {
  auth.logout()
  router.push('/login')
}

onMounted(() => { tick(); clockTimer = window.setInterval(tick, 1000) })
onUnmounted(() => clearInterval(clockTimer))
</script>

<style>
/* 布局框架（全局非 scoped：需覆盖插槽内容边距；类名均为布局专用无前缀冲突） */
.app { display: grid; grid-template-columns: 208px 1fr; height: 100vh; position: relative; z-index: 0; }

.side {
  background: var(--panel-solid); border-right: 1px solid var(--line);
  display: flex; flex-direction: column; padding: 14px 0;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%);
}
.logo { display: flex; gap: 10px; align-items: center; padding: 4px 16px 16px; border-bottom: 1px solid var(--line); }
.logo-mark {
  width: 34px; height: 34px; flex: none; position: relative;
  border: 1px solid var(--cyan); transform: rotate(45deg);
  background: linear-gradient(135deg, rgba(0,200,255,.25), transparent);
  box-shadow: 0 0 12px rgba(0,200,255,.35);
}
.logo-mark::after {
  content: ''; position: absolute; inset: 6px; border: 1px solid rgba(0,200,255,.6);
}
.logo b { font-size: 16px; letter-spacing: 1px; display: block; }
.logo small { color: var(--txt-dim); font-size: 9px; letter-spacing: 2px; }

.nav { margin-top: 12px; flex: 1; }
.nav a {
  display: flex; align-items: center; gap: 10px; padding: 10px 16px;
  color: var(--txt-dim); font-size: 13px; cursor: pointer; position: relative;
  border-left: 2px solid transparent; text-decoration: none;
}
.nav a:hover { color: var(--txt); background: rgba(0, 200, 255, .05); }
.nav a.on {
  color: var(--cyan); border-left-color: var(--cyan);
  background: linear-gradient(90deg, rgba(0,200,255,.12), transparent);
}
.nav .ico { width: 14px; height: 14px; border: 1px solid currentColor; opacity: .8; flex: none; }
.nav .badge {
  margin-left: auto; font-size: 10px; background: rgba(0,200,255,.15);
  border: 1px solid var(--cyan-dim); color: var(--cyan); padding: 1px 6px;
}

.side-foot { border-top: 1px solid var(--line); padding: 12px 16px; display: flex; gap: 10px; align-items: center; font-size: 12px; }
.avatar {
  width: 30px; height: 30px; flex: none; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #0a3a52, #062033); border: 1px solid var(--cyan-dim);
  color: var(--cyan); font-weight: 700;
}
.side-foot b { display: block; font-size: 12px; }
.side-foot small { color: var(--txt-dim); font-size: 10px; }
.logout-btn {
  margin-left: auto; background: transparent; border: 1px solid var(--line);
  color: var(--txt-dim); font-size: 11px; padding: 3px 8px; cursor: pointer;
}
.logout-btn:hover { color: var(--red); border-color: rgba(255,77,90,.5); }

.main { display: flex; flex-direction: column; min-width: 0; height: 100vh; overflow-y: auto; }
.main > *:not(.topbar):not(.mask):not(.drawer) { flex: none; margin: 0 16px 12px; }
.main > .topbar { margin: 0 0 12px; flex: none; }
.main > :nth-child(2) { margin-top: 12px; }

.topbar {
  display: flex; align-items: center; gap: 16px; padding: 0 16px;
  border-bottom: 1px solid var(--line); background: rgba(6, 14, 24, .7);
}
.sys-state {
  display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--green);
  font-family: var(--mono);
}
.sys-state .blink {
  width: 8px; height: 8px; border-radius: 50%; background: var(--green);
  box-shadow: 0 0 8px var(--green); animation: blink 1.6s infinite;
}
.sys-state.warn { color: var(--red); }
.sys-state.warn .blink { background: var(--red); box-shadow: 0 0 8px var(--red); }
@keyframes blink { 50% { opacity: .3; } }
.crumb { color: var(--txt-dim); font-size: 12px; }
.crumb b { color: var(--txt); }
.top-right { margin-left: auto; display: flex; align-items: center; gap: 14px; position: relative; }
.clock { font-family: var(--mono); color: var(--cyan); font-size: 13px; }
</style>
