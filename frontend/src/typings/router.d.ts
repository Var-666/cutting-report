import 'vue-router'
declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    role?: 'admin' | 'worker'
    title?: string
    icon?: string
    line?: 'cutting' | 'welding'
    keepAlive?: boolean
    multiTab?: boolean
    hideInMenu?: boolean
    activeMenu?: string
    i18nKey?: App.I18n.I18nKey
  }
}
