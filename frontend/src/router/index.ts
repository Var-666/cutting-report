import { lineHome, selectedLineType } from '../utils/productionLines.ts'
import { createRouter, createWebHistory } from 'vue-router'
import { loadSession, roleHome, session } from '../state/session'
import { installSessionExpiryRedirect } from '../state/sessionExpiry.ts'
import { cuttingRoutes } from './cuttingRoutes'
import { weldingRoutes } from './weldingRoutes.ts'
import { changeDevice, deviceSelection, loadDevices } from '../state/devices.ts'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: (to, from) => {
    if (to.path === from.path) return false
    document.querySelector('.soybean-layout main')?.scrollTo({ top: 0 })
    return { top: 0 }
  },
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
          meta: { title: '系统首页', icon: 'home', line: 'cutting' },
        },
        {
          path: 'reports',
          component: () => import('../layouts/ReportsLayout.vue'),
          meta: { title: '生产报表', icon: 'report', line: 'cutting' },
          children: [
            { path: '', redirect: '/admin/reports/leadership' },
            {
              path: 'leadership',
              name: 'leadership-report',
              component: () => import('../views/AdminLeadershipView.vue'),
              meta: { title: '领导报表', icon: 'chart', keepAlive: true },
            },
            {
              path: 'workshop',
              name: 'workshop-report',
              component: () => import('../views/AdminWorkshopView.vue'),
              meta: { title: '车间报表', icon: 'factory', keepAlive: true },
            },
            {
              path: 'equipment',
              name: 'equipment-report',
              component: () => import('../views/AdminEquipmentView.vue'),
              meta: { title: '设备报表', icon: 'zap', keepAlive: true },
            },
            {
              path: 'center',
              name: 'report-center',
              component: () => import('../views/ReportCenterView.vue'),
              meta: { title: '生成与历史', icon: 'clock' },
            },
          ],
        },
        {
          path: 'alarms',
          name: 'alarm-center',
          component: () => import('../views/AlarmCenterView.vue'),
          meta: { title: '智能告警中心', icon: 'bell', line: 'cutting' },
        },
        cuttingRoutes,
        weldingRoutes,
        {
          path: 'settings',
          component: () => import('../layouts/SettingsLayout.vue'),
          meta: { title: '系统设置', icon: 'settings' },
          children: [
            {
              path: '',
              name: 'settings',
              redirect: '/admin/settings/database',
            },
            {
              path: 'database',
              name: 'database',
              component: () => import('../views/DatabaseView.vue'),
              meta: { title: '数据库配置', icon: 'database' },
            },
            {
              path: 'configuration-diagnostics',
              name: 'configuration-diagnostics',
              component: () => import('../views/ConfigurationDiagnosticsView.vue'),
              meta: { title: '配置诊断', icon: 'settings' },
            },
            { path: 'report-design', redirect: '/admin/settings' },
            {
              path: 'automation',
              name: 'automation',
              component: () => import('../views/AutomationView.vue'),
              meta: { title: '报表自动化', icon: 'activity', line: 'cutting' },
            },
            {
              path: 'admins',
              name: 'admin-accounts',
              component: () => import('../views/AdminAccountsView.vue'),
              meta: { title: '管理员管理', icon: 'user' },
            },
            {
              path: 'model-usage',
              name: 'model-usage',
              component: () => import('../views/ModelUsageView.vue'),
              meta: { title: '模型用量', icon: 'sparkles' },
            },
          ],
        },
        { path: 'database', redirect: '/admin/settings/database' },
        { path: 'designer', redirect: '/admin/settings' },
        { path: 'automation', redirect: '/admin/settings/automation' },
        { path: 'admins', redirect: '/admin/settings/admins' },
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
  if (requiredRole === 'admin') {
    await loadDevices({ includeLines: true })
    const type = to.matched.find(record => record.meta.line)?.meta.line
    if (type && type !== selectedLineType(deviceSelection.items, deviceSelection.id)) {
      const line = deviceSelection.items.find(item => (item.type || 'cutting') === type)
      if (!line) return lineHome(deviceSelection.items, deviceSelection.id)
      changeDevice(line.id)
    }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 切割生产智能产报系统`
    : '切割生产智能产报系统'
})

installSessionExpiryRedirect(router)

export default router
