import type { Ref } from 'vue'
import type { ConfirmOptions } from './useConfirm'
import type { EmailDelivery } from '../types/automation'
import { computed, ref } from 'vue'
import { listEmailDeliveries, retryEmailDelivery } from '../api/automation'
import { toError } from '../utils/errors'

export function useAutomationDeliveries({ message, error, refresh, isDisposed, confirm }: {
  message: Ref<string>
  error: Ref<string>
  refresh: () => Promise<void>
  isDisposed: () => boolean
  confirm: (options: ConfirmOptions) => Promise<boolean>
}) {
  const pageSize = 10
  const deliveries = ref<EmailDelivery[]>([])
  const deliveryPage = ref(1)
  const deliveryTotal = ref(0)
  const deliveryLoading = ref(false)
  const deliverySearch = ref('')
  const appliedDeliverySearch = ref('')
  let deliveryController: AbortController | null = null
  let deliverySequence = 0
  const selectedDelivery = ref<EmailDelivery | null>(null)
  const resolutionDirty = ref(false)
  const resolutionSaving = ref(false)
  const retrying = new Set<string>()
  const pageCount = computed(() => Math.max(1, Math.ceil(deliveryTotal.value / pageSize)))

  async function loadDeliveries(page = deliveryPage.value, search = appliedDeliverySearch.value) {
    if (isDisposed() || !Number.isSafeInteger(page)) return
    let target = Math.min(Math.max(page, 1), pageCount.value)
    deliveryController?.abort()
    const controller = new AbortController()
    const sequence = ++deliverySequence
    deliveryController = controller
    deliveryLoading.value = true
    error.value = ''
    const current = () => !isDisposed() && !controller.signal.aborted && sequence === deliverySequence
    const query = search.trim()
    try {
      let data = await listEmailDeliveries({ page: target, pageSize, search: query }, { signal: controller.signal })
      while (current() && target > Math.max(1, Math.ceil(data.total / pageSize))) {
        target = Math.max(1, Math.ceil(data.total / pageSize))
        data = await listEmailDeliveries({ page: target, pageSize, search: query }, { signal: controller.signal })
      }
      if (!current()) return
      deliveries.value = data.items
      deliveryTotal.value = data.total
      deliveryPage.value = data.page
      appliedDeliverySearch.value = query
    } catch (caught) {
      const exception = toError(caught)
      if (current() && exception.name !== 'AbortError') error.value = exception.message
    } finally {
      if (sequence === deliverySequence) { deliveryLoading.value = false; deliveryController = null }
    }
  }

  function searchDeliveries() {
    return loadDeliveries(1, deliverySearch.value)
  }

  function clearDeliverySearch() {
    deliverySearch.value = ''
    return loadDeliveries(1, '')
  }

  function changeDeliveryPage(page: number) {
    if (deliveryLoading.value || page < 1 || page > pageCount.value || page === deliveryPage.value) return
    return loadDeliveries(page)
  }

  async function retry(item: EmailDelivery) {
    if (retrying.has(item.delivery_id)) return
    retrying.add(item.delivery_id)
    try {
      await retryEmailDelivery(item.delivery_id)
      if (isDisposed()) return
      message.value = '邮件已重新加入发送队列'
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      if (!isDisposed()) error.value = exception.message
    } finally {
      retrying.delete(item.delivery_id)
    }
  }

  async function openDelivery(item: EmailDelivery) {
    if (resolutionSaving.value || selectedDelivery.value?.delivery_id === item.delivery_id) return
    if (resolutionDirty.value && !await confirm({
      title: '放弃邮件核对？', content: '切换记录将丢弃当前核对结果和依据。',
      positiveText: '放弃修改', negativeText: '继续编辑', closable: false,
    })) return
    if (isDisposed() || resolutionSaving.value) return
    resolutionDirty.value = false
    selectedDelivery.value = item
  }

  async function deliveryResolved() {
    resolutionDirty.value = false
    message.value = '核对结果已保存；未接收的邮件仍需单独手动重试。'
    await refresh()
    if (isDisposed()) return
    selectedDelivery.value = deliveries.value.find((item) => item.delivery_id === selectedDelivery.value?.delivery_id) || selectedDelivery.value
  }

  function pauseDeliveries() {
    deliverySequence += 1
    deliveryController?.abort()
  }

  return { deliveries, deliveryPage, deliveryTotal, deliveryLoading, deliverySearch, appliedDeliverySearch, searchDeliveries, clearDeliverySearch, selectedDelivery, resolutionDirty, resolutionSaving, pageCount, loadDeliveries, changeDeliveryPage, retry, openDelivery, deliveryResolved, pauseDeliveries }
}
