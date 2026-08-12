/** Vue Router 路由配置。 */

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: '素材库' },
    },
    {
      path: '/search',
      name: 'search',
      component: () => import('@/views/SearchView.vue'),
      meta: { title: '高级搜索' },
    },
    {
      path: '/upload',
      name: 'upload',
      component: () => import('@/views/UploadView.vue'),
      meta: { title: '上传素材' },
    },
    {
      path: '/detail/:id',
      name: 'detail',
      component: () => import('@/views/DetailView.vue'),
      meta: { title: '素材详情' },
    },
    {
      path: '/scraper',
      name: 'scraper',
      component: () => import('@/views/ScraperView.vue'),
      meta: { title: '采集管理' },
    },
    {
      path: '/tags',
      name: 'tags',
      component: () => import('@/views/TagManageView.vue'),
      meta: { title: '标签管理' },
    },
    {
      path: '/models',
      name: 'models',
      component: () => import('@/views/ModelManageView.vue'),
      meta: { title: 'AI 模型管理' },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('@/views/AdminView.vue'),
      meta: { title: '素材管理' },
    },
  ],
})

// 更新页面标题
router.afterEach((to) => {
  document.title = `${to.meta.title || 'AI 穿搭素材库'} - AI 穿搭素材库`
})

export default router
