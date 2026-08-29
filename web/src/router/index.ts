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
      path: '/upload',
      name: 'upload',
      component: () => import('@/views/UploadView.vue'),
      meta: { title: '上传素材' },
    },
    {
      path: '/collections',
      name: 'collections',
      component: () => import('@/views/CollectionsView.vue'),
      meta: { title: '收藏合集' },
    },
    {
      path: '/model-photos',
      name: 'model-photos',
      component: () => import('@/views/ModelPhotoUploadView.vue'),
      meta: { title: '添加模特照片' },
    },
    {
      path: '/search',
      name: 'search',
      component: () => import('@/views/SearchView.vue'),
      meta: { title: '高级搜索' },
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
      path: '/tags/advanced',
      name: 'tags-advanced',
      component: () => import('@/views/TagAdvancedManageView.vue'),
      meta: { title: '标签高级管理' },
    },
    {
      path: '/persons',
      name: 'persons',
      component: () => import('@/views/PersonView.vue'),
      meta: { title: '人物管理' },
    },
    {
      // Arco Design 试点页：与 Naive UI 版并存对比，评估后决定是否迁移
      path: '/persons-arco',
      name: 'persons-arco',
      component: () => import('@/views/ArcoPersonPilotView.vue'),
      meta: { title: '人物管理（Arco 试点）' },
    },
    {
      path: '/persons/:id',
      name: 'person-detail',
      component: () => import('@/views/PersonDetailView.vue'),
      meta: { title: '人物详情' },
    },
    {
      path: '/face-scan',
      name: 'face-scan',
      component: () => import('@/views/FaceScanView.vue'),
      meta: { title: '人脸库扫描' },
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
    {
      path: '/admin/governance',
      name: 'admin-governance',
      component: () => import('@/views/GovernanceView.vue'),
      meta: { title: '数据治理' },
    },
    {
      path: '/admin/insights',
      name: 'admin-insights',
      component: () => import('@/views/InsightsView.vue'),
      meta: { title: '数据洞察' },
    },
    {
      path: '/tasks',
      name: 'tasks',
      component: () => import('@/views/TaskManageView.vue'),
      meta: { title: '任务管理' },
    },
    {
      // 404 兜底：未知路径重定向首页，避免白屏
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

// 更新页面标题
router.afterEach((to) => {
  document.title = `${to.meta.title || 'AI 穿搭素材库'} - AI 穿搭素材库`
})

export default router
