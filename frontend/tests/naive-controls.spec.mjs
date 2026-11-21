// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { h, KeepAlive, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter, isNavigationFailure, NavigationFailureType, RouterView } from 'vue-router'
import ReportFeedback from '../src/components/ReportFeedback.vue'
import ReportPanelState from '../src/components/ReportPanelState.vue'
import { useConfirm } from '../src/composables/useConfirm'
import { useUnsavedChanges } from '../src/composables/useUnsavedChanges'

const warning = vi.hoisted(() => vi.fn())
vi.mock('naive-ui', async importOriginal => ({
  ...await importOriginal(),
  useDialog: () => ({ warning }),
}))

enableAutoUnmount(afterEach)
beforeEach(() => { warning.mockReset(); warning.mockImplementation(() => ({ destroy: vi.fn() })) })

test('report feedback announces errors and forwards retry and dismissal', async () => {
  const wrapper = mount(ReportFeedback, { props: {
    type: 'error', title: '读取失败', message: '请重新查询', actionLabel: '重试', dismissible: true,
  } })
  const alert = wrapper.get('[role="alert"]')
  expect(alert.attributes('aria-live')).toBe('polite')
  expect(alert.text()).toContain('读取失败')
  expect(alert.text()).toContain('请重新查询')
  const retry = alert.findAll('button').find(button => button.text() === '重试')
  await retry.trigger('click')
  await alert.get('button[aria-label="关闭提示"]').trigger('click')
  expect(wrapper.emitted('action')).toEqual([[]])
  expect(wrapper.emitted('dismiss')).toEqual([[]])
})

test.each([['loading', 'status'], ['empty', 'status'], ['error', 'alert']])('report panel announces %s and makes recovery actionable', async (type, role) => {
  const wrapper = mount(ReportPanelState, { props: { type, title: '当前状态', message: '状态说明', actionLabel: '重新查询' } })
  const feedback = wrapper.get(`[role="${role}"]`)
  expect(feedback.text()).toContain('当前状态')
  expect(feedback.text()).toContain('状态说明')
  const button = feedback.get('button')
  expect(button.text()).toBe('重新查询')
  await button.trigger('click')
  expect(wrapper.emitted('action')).toEqual([[]])
})

function finishDialog(accepted) {
  const [dialog] = warning.mock.lastCall
  if (accepted) dialog.onPositiveClick()
  dialog.onAfterLeave()
}

test('confirmation accepts or rejects a decision and dismisses pending work when a cached page is hidden', async () => {
  const shown = ref(true)
  let confirm
  const Panel = { setup() { confirm = useConfirm(); return () => null } }
  mount({ render: () => h(KeepAlive, {}, { default: () => shown.value ? h(Panel) : null }) })
  const accepted = confirm('执行此操作')
  warning.mock.lastCall[0].onPositiveClick()
  expect(await confirm('动画退出期间重复打开')).toBe(false)
  warning.mock.lastCall[0].onAfterLeave()
  expect(await accepted).toBe(true)
  const dismissed = confirm({ title: '删除记录？', content: '记录 A', positiveText: '删除记录', maskClosable: true })
  expect(warning.mock.lastCall[0]).toMatchObject({ title: '删除记录？', positiveText: '删除记录', maskClosable: true })
  finishDialog(false)
  expect(await dismissed).toBe(false)
  const pending = confirm('离开页面')
  const { destroy } = warning.mock.results.at(-1).value
  shown.value = false
  await nextTick()
  expect(await pending).toBe(false)
  expect(destroy).toHaveBeenCalledOnce()
})

test('a dirty draft guards closing, navigation and unload until resolved and saving completes', async () => {
  const dirty = ref(false)
  const saving = ref(false)
  let discard
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { setup() { discard = useUnsavedChanges(dirty, '离开将丢弃修改。', saving); return () => null } } },
      { path: '/other', component: { render: () => null } },
    ],
  })
  await router.push('/')
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  const unloadAllowed = () => window.dispatchEvent(new Event('beforeunload', { cancelable: true }))
  expect(discard()).toBe(true)
  expect(unloadAllowed()).toBe(true)
  dirty.value = true
  expect(unloadAllowed()).toBe(false)
  const first = discard('关闭将丢弃草稿。')
  expect(discard()).toBe(false)
  expect(isNavigationFailure(await router.push('/other'), NavigationFailureType.aborted)).toBe(true)
  expect(warning.mock.lastCall[0]).toMatchObject({ negativeText: '继续编辑', positiveText: '放弃修改' })
  finishDialog(false)
  expect(await first).toBe(false)
  expect(dirty.value).toBe(true)
  const navigation = router.push('/other')
  await flushPromises()
  saving.value = true
  finishDialog(true)
  expect(isNavigationFailure(await navigation, NavigationFailureType.aborted)).toBe(true)
  expect(discard()).toBe(false)
  saving.value = false
  const approved = discard()
  finishDialog(true)
  expect(await approved).toBe(true)
  dirty.value = false
  expect(unloadAllowed()).toBe(true)
  dirty.value = true
  const stale = discard()
  wrapper.unmount()
  expect(await stale).toBe(false)
  expect(unloadAllowed()).toBe(true)
})
