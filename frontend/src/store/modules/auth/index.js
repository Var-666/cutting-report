import { session } from '../../../state/session.js'
export function useAuthStore() {
  return { get userInfo() { return { userName: session.user?.display_name || session.user?.username || '' } } }
}
