import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { setup as setupCssr } from '@css-render/vue3-ssr'

const components = import.meta.glob('../src/{components,views,layouts}/**/*.vue')

export async function renderComponent(path, props = {}, slots = {}) {
  const { default: component } = await components[`../src/${path}.vue`]()
  const app = createSSRApp({ render: () => h(component, props, slots) })
  setupCssr(app)
  return renderToString(app)
}
