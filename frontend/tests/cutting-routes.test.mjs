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

test('old URL and named route both enter file downloads', async () => {
  const router = routerForTest()
  await router.push('/admin/cutting-dispatch')
  assert.equal(router.currentRoute.value.name, 'cutting-downloads')
  assert.equal(router.currentRoute.value.path, '/admin/cutting-dispatch/downloads')
  await router.push('/admin/cutting-dispatch/push')
  await router.push({ name: 'cutting-dispatch' })
  assert.equal(router.currentRoute.value.name, 'cutting-downloads')
})

test('download and push pages have distinct state keys, titles and actions', async () => {
  const router = routerForTest()
  for (const [path, name, title, mode] of [
    ['downloads', 'cutting-downloads', '文件下载', 'download'],
    ['push', 'cutting-push', '文件推送', 'push'],
  ]) {
    await router.push(`/admin/cutting-dispatch/${path}`)
    assert.equal(router.currentRoute.value.name, name)
    assert.equal(router.currentRoute.value.meta.title, title)
    assert.deepEqual(router.currentRoute.value.matched.at(-1).props.default, { mode })
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

test('refreshable deep links resolve without first visiting parent', () => {
  for (const path of ['downloads', 'push']) {
    const route = routerForTest().resolve(`/admin/cutting-dispatch/${path}`)
    assert.equal(route.matched.length, 3)
    assert.equal(route.redirectedFrom, undefined)
    assert.ok(route.name)
  }
})
