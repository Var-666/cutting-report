import { session } from '../../../state/session.ts'
export function useAuthStore() {
  return { get userInfo() { return { userName: session.user?.display_name || session.user?.username || '' } } }
}
