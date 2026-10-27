import type { Router } from 'vue-router'
import { setSessionExpiredHandler } from '../api/sessionExpiry.ts'
import { expireSession } from './session.ts'

export function installSessionExpiryRedirect(router: Router) {
  return setSessionExpiredHandler(async () => {
    const current = router.currentRoute.value
    const shouldRemember = current?.matched?.some((record) => !record.meta.public)
      && current.fullPath
      && current.fullPath !== '/'
    const redirect = shouldRemember ? current.fullPath : ''

    expireSession()
    if (current?.name === 'login') return
    await router.replace({
      name: 'login',
      query: redirect ? { redirect } : {},
    })
  })
}
