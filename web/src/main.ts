/** Vue 应用入口：初始化 Arco Design、Router、Pinia 并挂载。 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'
import '@arco-design/web-vue/dist/arco.css'
import '@/styles/arco-theme.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
// Arco Design 全量注册（迁移完成，naive-ui 依赖已在 package.json 移除）
app.use(ArcoVue)

app.mount('#app')
