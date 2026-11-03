import { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { setup as setupCssr } from '@css-render/vue3-ssr'

let server
after(async () => { if (server) await (await server).close() })

// Use the application's SFC compiler and imports instead of extracting private functions.
async function loadComponent(path) {
  server ??= createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    appType: 'custom',
  })
  return (await (await server).ssrLoadModule(`/src/${path}.vue`)).default
}

export async function renderComponent(path, props = {}, slots = {}) {
  const component = await loadComponent(path)
  const app = createSSRApp({ render: () => h(component, props, slots) })
  setupCssr(app)
  return renderToString(app)
}
