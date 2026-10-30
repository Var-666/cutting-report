import type { RouteRecordRaw } from 'vue-router'
const view = () => import('../views/WeldingReportView.vue')

export const weldingRoutes: RouteRecordRaw = {
  path: 'welding',
  component: () => import('../layouts/WeldingLayout.vue'),
  meta: { line: 'welding' },
  children: [
    { path: '', redirect: '/admin/welding/home' },
    { path: 'home', name: 'welding-home', component: () => import('../views/WeldingHomeView.vue'), meta: { title: '生产首页', icon: 'home' } },
    { path: 'reports', meta: { title: '生产报表', icon: 'report' }, children: [
      { path: 'daily', name: 'welding-daily', component: view, props: { kind: 'daily' }, meta: { title: '生产统计报表', icon: 'chart' } },
      { path: 'parts', name: 'welding-parts', component: view, props: { kind: 'parts' }, meta: { title: '工件生产报表', icon: 'factory' } },
      { path: 'jobs', name: 'welding-jobs', component: view, props: { kind: 'jobs' }, meta: { title: '焊接作业报表', icon: 'activity' } },
      { path: 'equipment', name: 'welding-equipment', component: view, props: { kind: 'equipment' }, meta: { title: '设备效能报表', icon: 'zap' } },
    ] },
    { path: 'analysis', redirect: to => ({ path: '/admin/welding/reports/daily', query: to.query }) },
  ],
}
