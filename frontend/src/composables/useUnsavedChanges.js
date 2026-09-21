import { onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export function useUnsavedChanges(dirty, message = '当前页面有未保存的修改，确定离开吗？') {
  function beforeUnload(event) {
    if (!dirty.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

  onBeforeRouteLeave((to, from) => {
    if (to.path === from.path || !dirty.value) return true
    return window.confirm(message)
  })
}
