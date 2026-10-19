import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
    { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
    { path: '/nodes', name: 'nodes', component: () => import('../views/Nodes.vue') },
    { path: '/terminal', name: 'terminal', component: () => import('../views/Terminal.vue') },
    { path: '/tenants', name: 'tenants', component: () => import('../views/Tenants.vue') },
    { path: '/rules', name: 'rules', component: () => import('../views/Rules.vue') },
    { path: '/audit', name: 'audit', component: () => import('../views/Audit.vue') },
    { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') }
  ]
})

router.beforeEach((to) => {
  // 直接读 localStorage 而非 store（store 在 router 注册时尚未实例化，auth.token 永远为空）
  const token = localStorage.getItem('hs_token') || (window as any).__hs_token__ || ''
  if (to.name !== 'login' && !token) return { name: 'login' }
})

export default router
