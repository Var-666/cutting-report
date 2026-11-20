import { onBeforeUnmount, onDeactivated } from 'vue'
import { useDialog, type DialogOptions } from 'naive-ui'

export type ConfirmOptions = Pick<DialogOptions, 'title' | 'positiveText' | 'negativeText' | 'type' | 'closable' | 'maskClosable'> & {
  content: string
}

export function useConfirm() {
  const dialog = useDialog()
  let cancelPending: (() => void) | undefined
  onBeforeUnmount(() => cancelPending?.())
  onDeactivated(() => cancelPending?.())

  return (input: string | ConfirmOptions): Promise<boolean> => {
    if (cancelPending) return Promise.resolve(false)
    const { type = 'warning', maskClosable = false, ...options } = typeof input === 'string' ? { content: input } : input
    return new Promise<boolean>((resolve) => {
      let accepted = false
      function finish(accepted: boolean) {
        if (cancelPending !== cancel) return
        cancelPending = undefined
        resolve(accepted)
      }
      function cancel() { finish(false); instance.destroy() }
      const instance = dialog[type === 'default' ? 'create' : type]({
        title: '请确认操作', positiveText: '确认', negativeText: '取消',
        ...options,
        type, maskClosable,
        onPositiveClick: () => { accepted = true },
        onAfterLeave: () => finish(accepted),
      })
      cancelPending = cancel
    })
  }
}
