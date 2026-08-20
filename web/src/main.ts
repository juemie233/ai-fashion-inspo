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

// 全局错误兜底：组件树未捕获的渲染/异步错误统一记录，避免静默失败。
// 渲染错误的用户可见降级由 ErrorBoundary 组件承担（App.vue 已包裹路由视图）。
app.config.errorHandler = (err, _instance, info) => {
  console.error('[Vue 全局错误]', info, err)
}

app.mount('#app')
