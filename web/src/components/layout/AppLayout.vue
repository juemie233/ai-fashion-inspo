<script setup lang="ts">
/** 应用整体布局：侧边导航 + 主内容区。 */

import { computed, h, type Component } from 'vue'
import { useRoute } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  ImagesOutline,
  SearchOutline,
  CloudUploadOutline,
  CameraOutline,
  PricetagsOutline,
  ScanOutline,
  HardwareChipOutline,
  SettingsOutline,
  ListOutline,
  PersonOutline,
  ShieldCheckmarkOutline,
  BarChartOutline,
} from '@vicons/ionicons5'
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

function renderIcon(icon: Component) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

/** 菜单分组：高频「浏览」操作在前，低频「管理」项收纳在后，降低认知负担 */
const menuOptions = [
  {
    type: 'group' as const,
    label: '浏览',
    key: 'browse',
    children: [
      { label: '素材库', key: 'home', icon: renderIcon(ImagesOutline) },
      { label: '上传素材', key: 'upload', icon: renderIcon(CloudUploadOutline) },
      { label: '添加模特照片', key: 'model-photos', icon: renderIcon(CameraOutline) },
      { label: '高级搜索', key: 'search', icon: renderIcon(SearchOutline) },
    ],
  },
  {
    type: 'group' as const,
    label: '管理',
    key: 'manage',
    children: [
      { label: '标签管理', key: 'tags', icon: renderIcon(PricetagsOutline) },
      { label: '人物管理', key: 'persons', icon: renderIcon(PersonOutline) },
      { label: '采集管理', key: 'scraper', icon: renderIcon(ScanOutline) },
      { label: 'AI 模型', key: 'models', icon: renderIcon(HardwareChipOutline) },
      { label: '素材管理', key: 'admin', icon: renderIcon(SettingsOutline) },
      { label: '数据治理', key: 'admin-governance', icon: renderIcon(ShieldCheckmarkOutline) },
      { label: '数据洞察', key: 'admin-insights', icon: renderIcon(BarChartOutline) },
      { label: '任务管理', key: 'tasks', icon: renderIcon(ListOutline) },
    ],
  },
]
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <n-menu
        :collapsed-width="64"
        :collapsed-icon-size="22"
        :options="menuOptions"
        @update:value="(key: string) => $router.push({ name: key })"
        :value="menuKey"
      />
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
