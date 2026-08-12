<script setup lang="ts">
/** 应用整体布局：侧边导航 + 主内容区。 */

import { h, type Component } from 'vue'
import { NIcon } from 'naive-ui'
import {
  ImagesOutline,
  SearchOutline,
  CloudUploadOutline,
  PricetagsOutline,
  ScanOutline,
  HardwareChipOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

function renderIcon(icon: Component) {
  return () => h(NIcon, null, { default: () => h(icon) })
}
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <n-menu
        :collapsed-width="64"
        :collapsed-icon-size="22"
        :options="[
          { label: '灵感库', key: 'home', icon: renderIcon(ImagesOutline) },
          { label: '高级搜索', key: 'search', icon: renderIcon(SearchOutline) },
          { label: '上传素材', key: 'upload', icon: renderIcon(CloudUploadOutline) },
          { label: '采集管理', key: 'scraper', icon: renderIcon(ScanOutline) },
          { label: '标签管理', key: 'tags', icon: renderIcon(PricetagsOutline) },
          { label: 'AI 模型', key: 'models', icon: renderIcon(HardwareChipOutline) },
          { label: '素材管理', key: 'admin', icon: renderIcon(SettingsOutline) },
        ]"
        @update:value="(key: string) => $router.push(`/${key === 'home' ? '' : key}`)"
        :default-value="$route.name as string"
      />
    </aside>

    <!-- 主内容区 -->
    <main class="main-content">
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
