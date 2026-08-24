import { createRouter, createWebHistory } from 'vue-router'
import { loadSession, roleHome, session } from '../state/session'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginPageView.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/admin',
      component: () => import('../layouts/AdminLayout.vue'),
      meta: { role: 'admin' },
      children: [
        { path: '', redirect: '/admin/home' },
        {
          path: 'home',
          name: 'admin-home',
          component: () => import('../views/AdminHomeView.vue'),
          meta: { title: '系统首页' },
        },
        {
          path: 'reports',
          component: () => import('../layouts/ReportsLayout.vue'),
          children: [
            { path: '', redirect: '/admin/reports/leadership' },
            {
              path: 'leadership',
              name: 'leadership-report',
              component: () => import('../views/AdminLeadershipView.vue'),
              meta: { title: '领导报表' },
            },
            {
              path: 'workshop',
              name: 'workshop-report',
              component: () => import('../views/AdminWorkshopView.vue'),
              meta: { title: '车间报表' },
            },
            {
              path: 'equipment',
              name: 'equipment-report',
              component: () => import('../views/AdminEquipmentView.vue'),
              meta: { title: '设备报表' },
            },
            {
              path: 'center',
              name: 'report-center',
              component: () => import('../views/ReportCenterView.vue'),
              meta: { title: '生成与历史' },
            },
          ],
        },
        {
          path: 'database',
          name: 'database',
          component: () => import('../views/DatabaseView.vue'),
          meta: { title: '数据库管理' },
        },
        {
          path: 'designer',
          name: 'designer',
          component: () => import('../views/DesignerView.vue'),
          meta: { title: '报表设计' },
        },
      ],
    },
    {
      path: '/worker',
      component: () => import('../layouts/WorkerLayout.vue'),
      meta: { role: 'worker' },
      children: [
        { path: '', redirect: '/worker/tasks' },
        {
          path: 'tasks',
          name: 'worker-tasks',
          component: () => import('../views/WorkerTasksView.vue'),
          meta: { title: '我的任务确认' },
        },
      ],
    },
    { path: '/', redirect: '/login' },
    { path: '/:pathMatch(.*)*', redirect: '/login' },
  ],
})

router.beforeEach(async (to) => {
  await loadSession()

  if (to.meta.public) {
    if (session.user) return roleHome(session.user.role)
    return true
  }

  if (!session.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const requiredRole = to.matched.find((record) => record.meta.role)?.meta.role
  if (requiredRole && session.user.role !== requiredRole) {
    return roleHome(session.user.role)
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 切割生产智能产报系统`
    : '切割生产智能产报系统'
})

export default router
