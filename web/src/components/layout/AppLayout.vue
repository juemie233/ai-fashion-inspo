<script setup lang="ts">
/** 应用整体布局：侧边导航 + 主内容区。 */

import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  IconImage,
  IconSearch,
  IconUpload,
  IconCamera,
  IconTags,
  IconScan,
  IconDesktop,
  IconSettings,
  IconList,
  IconUser,
  IconSafe,
  IconBarChart,
} from '@arco-design/web-vue/es/icon'
import SchemaVersionBanner from './SchemaVersionBanner.vue'

const route = useRoute()

/** 详情类路由映射回所属一级菜单，保持侧边栏高亮（如人物详情 → 人物管理） */
const menuKey = computed(() => {
  const name = route.name as string
  const mapping: Record<string, string> = {
    'person-detail': 'persons',
    detail: 'home',
  }
  return mapping[name] ?? name
})
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <a-menu
        :selected-keys="[menuKey]"
        auto-open-selected
        @menu-item-click="(key: string) => $router.push({ name: key })"
      >
        <a-menu-item-group title="浏览">
          <a-menu-item key="home"><IconImage />素材库</a-menu-item>
          <a-menu-item key="upload"><IconUpload />上传素材</a-menu-item>
          <a-menu-item key="model-photos"><IconCamera />添加模特照片</a-menu-item>
          <a-menu-item key="search"><IconSearch />高级搜索</a-menu-item>
        </a-menu-item-group>
        <a-menu-item-group title="管理">
          <a-menu-item key="tags"><IconTags />标签管理</a-menu-item>
          <a-menu-item key="persons"><IconUser />人物管理</a-menu-item>
          <a-menu-item key="scraper"><IconScan />采集管理</a-menu-item>
          <a-menu-item key="models"><IconDesktop />AI 模型</a-menu-item>
          <a-menu-item key="admin"><IconSettings />素材管理</a-menu-item>
          <a-menu-item key="admin-governance"><IconSafe />数据治理</a-menu-item>
          <a-menu-item key="admin-insights"><IconBarChart />数据洞察</a-menu-item>
          <a-menu-item key="tasks"><IconList />任务管理</a-menu-item>
        </a-menu-item-group>
      </a-menu>
    </aside>

    <!-- 主内容区 -->
    <main class="main-content">
      <!-- 前后端 schema 版本握手提示 -->
      <SchemaVersionBanner />
      <slot />
    </main>
  </div>
</template>

<style scoped>
/* 外壳：flex 布局，占据全屏 */
.app-shell {
  display: flex;
  width: 100%;
  min-height: 100vh;
}

/* 侧边栏：固定宽度 */
.sidebar {
  width: 200px;
  min-width: 200px;
  flex-shrink: 0;
  border-right: 1px solid #e5e7eb;
  background: #fff;
}

/* 主内容区：占据剩余空间，可垂直滚动 */
.main-content {
  flex: 1;
  min-width: 0;
  padding: 24px;
  overflow-y: auto;
  overflow-x: hidden;
  background: #f9fafb;
  box-sizing: border-box;
}
</style>
