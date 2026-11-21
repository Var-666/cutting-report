// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { flushPromises, mount } from '@vue/test-utils'
import { test } from 'vitest'
import * as Vue from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import AdminPageView from '../src/layouts/AdminPageView.vue'
import ReportsLayout from '../src/layouts/ReportsLayout.vue'

test('admin navigation retains the three report pages across other modules and discards them on device change', async t => {
  const device = Vue.ref('Cutter01'), mounted = [], destroyed = [], active = new Set()
  let settingsReads = 0
  const report = kind => Vue.defineComponent({ setup() {
    const identity = `${device.value}:${kind}`
    mounted.push(identity)
    Vue.onActivated(() => active.add(identity))
    Vue.onDeactivated(() => active.delete(identity))
    Vue.onBeforeUnmount(() => { destroyed.push(identity); active.delete(identity) })
    return () => Vue.h('p', identity)
  } })
  const other = { render: () => Vue.h('p', '其他功能') }
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/admin', component: { setup: () => {
      const route = useRoute()
      return () => Vue.h(AdminPageView, { ready: true, deviceId: device.value,
        devicePage: route.path.startsWith('/admin/reports') || route.path === '/admin/home', transitionName: '' })
    } }, children: [{ path: 'home', component: other }, { path: 'settings', component: RouterView,
      children: [{ path: 'database', component: { setup() { settingsReads++; return () => Vue.h('p', '数据库设置') } } }],
    }, {
      path: 'reports', component: ReportsLayout, children: [...['leadership', 'workshop', 'equipment'].map(kind => ({
        path: kind, name: kind, component: report(kind), meta: { keepAlive: true },
      })), { path: 'center', name: 'center', component: report('center') }],
    }],
  }] })
  await router.push('/admin/reports/workshop')
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  t.onTestFinished(() => wrapper.unmount())
  for (const kind of ['workshop', 'equipment', 'leadership']) {
    await router.push(`/admin/reports/${kind}`); await flushPromises()
    assert.deepEqual([...active], [`Cutter01:${kind}`])
    await router.push('/admin/home'); await flushPromises()
    assert.equal(active.size, 0, 'cached report work is paused outside the report module')
    assert.equal(destroyed.length, 0)
    await router.push(`/admin/reports/${kind}`); await flushPromises()
    assert.equal(mounted.filter(item => item === `Cutter01:${kind}`).length, 1)
  }
  await router.push('/admin/reports/center'); await flushPromises()
  await router.push('/admin/home'); await flushPromises()
  assert.ok(destroyed.includes('Cutter01:center'), 'noncached report jobs leave the page and stop their own work')
  await router.push('/admin/settings/database'); await flushPromises()
  assert.equal(settingsReads, 1, 'the inactive reports outlet must not mount a second settings page')
  await router.push('/admin/reports/leadership'); await flushPromises()
  device.value = 'Cutter02'
  await flushPromises()
  assert.deepEqual([...active], ['Cutter02:leadership'])
  assert.equal(destroyed.filter(item => item.startsWith('Cutter01:') && item !== 'Cutter01:center').length, 3)
  assert.equal(mounted.filter(item => item === 'Cutter02:leadership').length, 1)
})
