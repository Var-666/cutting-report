import type { RouteRecordRaw } from 'vue-router'
export const cuttingRoutes: RouteRecordRaw = {
  path: 'cutting-dispatch',
  meta: { line: 'cutting' },
  component: () => import('../layouts/CuttingLayout.vue'),
  children: [
    {
      path: '',
      name: 'cutting-dispatch',
      component: () => import('../views/CuttingDispatchView.vue'),
      meta: { title: '文件派发', icon: 'send' },
    },
    {
      path: 'downloads',
      name: 'cutting-downloads',
      redirect: { name: 'cutting-dispatch' },
    },
    {
      path: 'push',
      name: 'cutting-push',
      redirect: { name: 'cutting-dispatch' },
    },
  ],
}
