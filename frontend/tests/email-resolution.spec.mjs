// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NDialogProvider } from 'naive-ui'
import { afterEach, expect, test, vi } from 'vitest'
import { h } from 'vue'
import AppSelect from '../src/components/AppSelect.vue'
import EmailDeliveryResolution from '../src/components/EmailDeliveryResolution.vue'
import { setupSoybeanComponents } from '../src/vendor/soybean-admin/components'

const addresses = ['a@example.com', 'b@example.com']
const delivery = {
  delivery_id: 'd1', status: 'unknown', message_id: '<stable@host>',
  schedule_name: '日报', subject: '统计日报', recipients: addresses, recipient_results: {},
}

enableAutoUnmount(afterEach)

function mountResolution({ send = () => Response.json({ success: true, data: {} }),
  read = () => Response.json({ success: true, data: { items: [] } }), recipientResults = {} } = {}) {
  const requests = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('/cancel')) return Response.json({})
    if (url.endsWith('/d1/resolutions')) return read(init.signal)
    if (url.endsWith('/d1/resolve') && init.method === 'POST') {
      const body = JSON.parse(init.body)
      requests.push(body)
      return send()
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const wrapper = mount({
    render: () => h(NDialogProvider, {}, { default: () => h(EmailDeliveryResolution, {
      delivery: { ...delivery, recipient_results: recipientResults },
    }) }),
  }, {
    attachTo: document.body,
    global: { plugins: [{ install: setupSoybeanComponents }], components: { AppSelect } },
  })
  const panel = wrapper.getComponent(EmailDeliveryResolution)
  const field = () => panel.get('textarea[name="verification_evidence"]')
  async function choose(address, outcome) {
    const label = `${address} 接收结果`
    const select = panel.findAllComponents(AppSelect).find(component =>
      component.find('[role="combobox"]').attributes('aria-label') === label)
    if (!select) throw new Error(`Missing recipient control ${address}`)
    select.vm.$emit('update:modelValue', outcome)
    await panel.vm.$nextTick()
  }
  return { wrapper, panel, requests, field, choose,
    submit: () => panel.get('form').trigger('submit'),
    button: () => panel.get('button[type="submit"]'),
  }
}

test('verification requires every pending recipient and evidence and preserves the draft after an error', async () => {
  const page = mountResolution({ send: () => Response.json({ detail: '状态冲突' }, { status: 409 }) })
  await page.submit()
  await page.choose(addresses[0], 'accepted')
  await page.field().setValue(' 服务器日志编号 123 ')
  await page.submit()
  expect(page.button().element.disabled).toBe(true)
  expect(page.requests).toEqual([])
  await page.choose(addresses[1], 'not_accepted')
  expect(page.button().element.disabled).toBe(false)
  await page.submit()
  await flushPromises()
  expect(page.requests).toEqual([{ message_id: '<stable@host>', evidence_note: '服务器日志编号 123',
    outcomes: [{ recipient: addresses[0], outcome: 'accepted' }, { recipient: addresses[1], outcome: 'not_accepted' }],
  }])
  expect(page.field().element.value).toBe(' 服务器日志编号 123 ')
  expect(page.panel.get('[role="alert"]').text()).toContain('状态冲突')
  expect(page.panel.text()).toContain('服务器未接收')
  expect(page.panel.emitted('resolved')).toBeUndefined()
})

test('a pending verification saves once and leaves email retry as a separate action', async () => {
  let finish
  const page = mountResolution({ send: () => new Promise(resolve => { finish = resolve }) })
  await page.choose(addresses[0], 'accepted')
  await page.choose(addresses[1], 'not_accepted')
  await page.field().setValue('已核对 SMTP 日志')
  await page.submit()
  await page.submit()
  expect(page.requests).toHaveLength(1)
  expect(page.button().element.disabled).toBe(true)
  finish(Response.json({ success: true, data: { resolved: true } }))
  await flushPromises()
  expect(page.panel.find('form').exists()).toBe(false)
  expect(page.panel.emitted('resolved')).toEqual([[]])
  expect(page.panel.get('[role="status"]').text()).toContain('手动重试')
  expect(page.requests).toHaveLength(1)
})

test('leaving aborts the audit request and suppresses a late save completion', async () => {
  let finishRead, finishSave, signal
  const page = mountResolution({
    read: value => { signal = value; return new Promise(resolve => { finishRead = resolve }) },
    send: () => new Promise(resolve => { finishSave = resolve }),
  })
  await page.choose(addresses[0], 'accepted')
  await page.choose(addresses[1], 'accepted')
  await page.field().setValue('已核对')
  await page.submit()
  expect(page.panel.text()).toContain('正在读取核对记录')
  page.wrapper.unmount()
  expect(signal.aborted).toBe(true)
  finishRead(Response.json({ success: true, data: { items: [{ resolution_id: 'late' }] } }))
  finishSave(Response.json({ success: true, data: { resolved: true } }))
  await flushPromises()
  expect(page.panel.emitted('resolved')).toBeUndefined()
})

test('accepted recipients stay outside the payload and an audit failure stays visible after save', async () => {
  const page = mountResolution({
    recipientResults: { [addresses[0]]: { status: 'accepted' } },
    read: () => Response.json({ detail: '审计暂时不可用' }, { status: 502 }),
  })
  await flushPromises()
  expect(page.panel.get('[role="alert"]').text()).toContain('审计暂时不可用')
  await page.choose(addresses[1], 'accepted')
  await page.field().setValue('服务器确认接收')
  await page.submit()
  await flushPromises()
  expect(page.requests[0].outcomes).toEqual([{ recipient: addresses[1], outcome: 'accepted' }])
  expect(page.panel.get('[role="status"]').text()).toContain('核对已保存')
  expect(page.panel.get('[role="alert"]').text()).toContain('审计暂时不可用')
})
