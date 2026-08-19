/** Vue 应用入口：初始化 Naive UI、Arco Design（试点）、Router、Pinia 并挂载。 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import ArcoVue from '@arco-design/web-vue'
import '@arco-design/web-vue/dist/arco.css'
import '@/styles/arco-theme.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(naive)
// Arco Design 试点注册（仅试点页使用；评估后再决定全量引入或按需加载）
app.use(ArcoVue)

app.mount('#app')
