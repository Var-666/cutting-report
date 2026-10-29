import { onBeforeUnmount, onDeactivated } from 'vue'
import { useDialog } from 'naive-ui'

export function useConfirm() {
  const dialog = useDialog()
  const pending = new Set<() => void>()
  function cancelPending() { for (const cancel of pending) cancel() }
  onBeforeUnmount(cancelPending)
  onDeactivated(cancelPending)

  return (content: string) => new Promise<boolean>((resolve) => {
    function finish(accepted: boolean) { pending.delete(cancel); resolve(accepted) }
    function cancel() { finish(false); instance.destroy() }
    const instance = dialog.warning({
      title: '请确认操作', content, positiveText: '确认', negativeText: '取消',
      maskClosable: false,
      onPositiveClick: () => finish(true),
      onNegativeClick: () => finish(false),
      onClose: () => finish(false),
      onEsc: () => finish(false),
    })
    pending.add(cancel)
  })
}
