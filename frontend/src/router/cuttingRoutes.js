// Both child pages reuse the file picker, with independent state and actions.
export const cuttingRoutes = {
  path: 'cutting-dispatch',
  component: () => import('../layouts/CuttingLayout.vue'),
  children: [
    { path: '', name: 'cutting-dispatch', redirect: '/admin/cutting-dispatch/downloads' },
    {
      path: 'downloads',
      name: 'cutting-downloads',
      component: () => import('../views/CuttingDispatchView.vue'),
      props: { mode: 'download' },
      meta: { title: '文件下载' },
    },
    {
      path: 'push',
      name: 'cutting-push',
      component: () => import('../views/CuttingDispatchView.vue'),
      props: { mode: 'push' },
      meta: { title: '文件推送' },
    },
  ],
}
