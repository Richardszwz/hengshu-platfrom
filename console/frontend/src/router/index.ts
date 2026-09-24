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
    { path: '/rules', name: 'rules', component: () => import('../views/Rules.vue') }
  ]
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.name !== 'login' && !auth.token) return { name: 'login' }
})

export default router
