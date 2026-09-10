import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './assets/style.css'
import './assets/content-tables.css'
import { contentTable } from './directives/contentTable.js'
import AppSelect from './components/AppSelect.vue'
import AppDatePicker from './components/AppDatePicker.vue'

createApp(App)
  .component('AppSelect', AppSelect)
  .component('AppDatePicker', AppDatePicker)
  .directive('content-table', contentTable)
  .use(router)
  .mount('#app')
