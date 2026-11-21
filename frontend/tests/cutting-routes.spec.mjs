// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { mount } from '@vue/test-utils'
import { test } from 'vitest'
import { h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import CuttingLayout from '../src/layouts/CuttingLayout.vue'
import { cuttingRoutes } from '../src/router/cuttingRoutes.ts'

const page = { render: () => h('h1', '文件派发') }

test('file dispatch opens directly without a redundant one-item submenu', async t => {
  const router = routerForTest()
  await router.push('/admin/cutting-dispatch')
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  t.onTestFinished(() => wrapper.unmount())
  assert.equal(wrapper.get('h1').text(), '文件派发')
  assert.equal(wrapper.findAll('nav, [role="navigation"], a').length, 0)
})
function routerForTest() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/admin', component: RouterView, meta: { role: 'admin' },
      children: [{
        ...cuttingRoutes, component: CuttingLayout,
        children: cuttingRoutes.children.map((route) => route.component ? { ...route, component: page } : route),
      }],
    }],
  })
}

test('file dispatch is the single canonical page', async () => {
  const router = routerForTest()
  await router.push('/admin/cutting-dispatch')
  assert.equal(router.currentRoute.value.name, 'cutting-dispatch')
  assert.equal(router.currentRoute.value.path, '/admin/cutting-dispatch')
  assert.equal(router.currentRoute.value.meta.title, '文件派发')
})

test('old download and push deep links redirect to unified dispatch', async () => {
  const router = routerForTest()
  for (const path of ['downloads', 'push']) {
    await router.push(`/admin/cutting-dispatch/${path}`)
    assert.equal(router.currentRoute.value.name, 'cutting-dispatch')
    assert.equal(router.currentRoute.value.path, '/admin/cutting-dispatch')
    assert.equal(router.currentRoute.value.meta.title, '文件派发')
  }
})

test('both nested pages inherit admin authorization', () => {
  const router = routerForTest()
  for (const path of ['downloads', 'push']) {
    const route = router.resolve(`/admin/cutting-dispatch/${path}`)
    assert.equal(route.meta.role, 'admin')
    assert.equal(route.matched.find((record) => record.meta.role)?.meta.role, 'admin')
    assert.equal(route.matched[1].path, '/admin/cutting-dispatch')
  }
})

test('legacy deep links remain resolvable without first visiting parent', () => {
  for (const path of ['downloads', 'push']) {
    const route = routerForTest().resolve(`/admin/cutting-dispatch/${path}`)
    assert.equal(route.matched.length, 3)
    assert.equal(route.redirectedFrom, undefined)
    assert.ok(route.name)
  }
})
