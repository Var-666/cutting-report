import type { Ref } from 'vue'
import type { EmailDelivery } from '../types/automation'
import { computed, ref } from 'vue'
import { listEmailDeliveries, retryEmailDelivery } from '../api/automation'
import { toError } from '../utils/errors'

export function useAutomationDeliveries({ message, error, refresh, isDisposed, confirm }: {
  message: Ref<string>
  error: Ref<string>
  refresh: () => Promise<void>
  isDisposed: () => boolean
  confirm: (message: string) => Promise<boolean>
}) {
  const deliveries = ref<EmailDelivery[]>([])
  const deliveryPage = ref(1)
  const deliveryTotal = ref(0)
  const deliveryLoading = ref(false)
  let deliveryController: AbortController | null = null
  let deliverySequence = 0
  const selectedDelivery = ref<EmailDelivery | null>(null)
  const resolutionDirty = ref(false)
  const pageCount = computed(() => Math.max(1, Math.ceil(deliveryTotal.value / 20)))

  async function loadDeliveries(page = deliveryPage.value) {
    if (isDisposed() || !Number.isSafeInteger(page)) return
    let target = Math.min(Math.max(page, 1), pageCount.value)
    deliveryController?.abort()
    const controller = new AbortController()
    const sequence = ++deliverySequence
    deliveryController = controller
    deliveryLoading.value = true
    error.value = ''
    const current = () => !isDisposed() && !controller.signal.aborted && sequence === deliverySequence
    try {
      let data = await listEmailDeliveries({ page: target, pageSize: 20 }, { signal: controller.signal })
      while (current() && target > Math.max(1, Math.ceil(data.total / 20))) {
        target = Math.max(1, Math.ceil(data.total / 20))
        data = await listEmailDeliveries({ page: target, pageSize: 20 }, { signal: controller.signal })
      }
      if (!current()) return
      deliveries.value = data.items
      deliveryTotal.value = data.total
      deliveryPage.value = data.page
    } catch (caught) {
      const exception = toError(caught)
      if (current() && exception.name !== 'AbortError') error.value = exception.message
    } finally {
      if (sequence === deliverySequence) { deliveryLoading.value = false; deliveryController = null }
    }
  }

  function changeDeliveryPage(page: number) {
    if (deliveryLoading.value || page < 1 || page > pageCount.value || page === deliveryPage.value) return
    return loadDeliveries(page)
  }

  async function retry(item: EmailDelivery) {
    try {
      await retryEmailDelivery(item.delivery_id)
      message.value = '邮件已重新加入发送队列'
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message
    }
  }

  async function openDelivery(item: EmailDelivery) {
    if (resolutionDirty.value && !await confirm('核对内容尚未保存，确定切换记录吗？')) return
    resolutionDirty.value = false
    selectedDelivery.value = item
  }

  async function deliveryResolved() {
    resolutionDirty.value = false
    message.value = '核对结果已保存；未接收的邮件仍需单独手动重试。'
    await refresh()
    selectedDelivery.value = deliveries.value.find((item) => item.delivery_id === selectedDelivery.value?.delivery_id) || selectedDelivery.value
  }

  function pauseDeliveries() {
    deliverySequence += 1
    deliveryController?.abort()
  }

  return { deliveries, deliveryPage, deliveryTotal, deliveryLoading, selectedDelivery, resolutionDirty, pageCount, loadDeliveries, changeDeliveryPage, retry, openDelivery, deliveryResolved, pauseDeliveries }
}
