import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './assets/style.css'
import './assets/content-tables.css'
import { contentTable } from './directives/contentTable.js'

createApp(App).directive('content-table', contentTable).use(router).mount('#app')
