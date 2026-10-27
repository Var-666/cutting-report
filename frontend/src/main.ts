import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import 'virtual:uno.css'
import './assets/application.css'
import { contentTable } from './directives/contentTable.ts'
import AppSelect from './components/AppSelect.vue'
import AppDatePicker from './components/AppDatePicker.vue'
import { resetSetupStore } from './store/plugins/index.ts'
import { setupI18n } from './locales'
import { setupSoybeanComponents } from './vendor/soybean-admin/components.ts'

const app = createApp(App)
const pinia = createPinia()
pinia.use(resetSetupStore)
setupI18n(app)
setupSoybeanComponents(app)
app
  .use(pinia)
  .component('AppSelect', AppSelect)
  .component('AppDatePicker', AppDatePicker)
  .directive('content-table', contentTable)
  .use(router)
  .mount('#app')
