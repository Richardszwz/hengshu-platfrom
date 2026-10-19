import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './theme/dark.css'

// 全局错误兜底：任何渲染/运行时崩溃都显示错误条（不再白屏无提示）
function showFatal(msg: string) {
  let el = document.getElementById('hs-fatal')
  if (!el) {
    el = document.createElement('div')
    el.id = 'hs-fatal'
    el.style.cssText =
      'position:fixed;left:0;right:0;top:0;z-index:99999;background:#3a0a14;color:#ffb3c0;' +
      'font:12px/1.6 monospace;padding:8px 14px;border-bottom:1px solid #ff4d6a;white-space:pre-wrap;'
    document.body.appendChild(el)
  }
  el.textContent = '⚠ 页面渲染出错（请截图反馈）：' + msg
}
window.addEventListener('error', (e) => showFatal(`${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`))
window.addEventListener('unhandledrejection', (e) => showFatal('promise: ' + ((e.reason && e.reason.message) || e.reason)))

const app = createApp(App)
app.config.errorHandler = (err: any) => showFatal('vue: ' + (err?.message || err))
app.use(createPinia()).use(router).mount('#app')
