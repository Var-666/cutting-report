import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cuttingRoutes } from '../src/router/cuttingRoutes.js'

const stub = { render: () => null }

test('file submenu shares report navigation styles without a local override', () => {
  for (const name of ['CuttingLayout', 'ReportsLayout']) {
    const source = readFileSync(new URL(`../src/layouts/${name}.vue`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    assert.match(descriptor.template.content, /<section class="report-navigation"/)
    assert.equal(descriptor.styles.length, 0)
  }
})
function routerForTest() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/admin', component: stub, meta: { role: 'admin' },
      children: [{
        ...cuttingRoutes, component: stub,
        children: cuttingRoutes.children.map((route) => route.component ? { ...route, component: stub } : route),
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
