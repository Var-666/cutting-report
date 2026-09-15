export const cuttingRoutes = {
  path: 'cutting-dispatch',
  component: () => import('../layouts/CuttingLayout.vue'),
  children: [
    {
      path: '',
      name: 'cutting-dispatch',
      component: () => import('../views/CuttingDispatchView.vue'),
      meta: { title: '文件派发' },
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
