<script setup lang="ts">
/** 高级标签管理页：左侧 Tab 导航 + 右侧主工作区（各面板 KeepAlive 保活）。 */

import { computed } from 'vue'
import { useTagAdvanced } from '@/composables/useTagAdvanced'
import TagHealthPanel from '@/components/tag/advanced/TagHealthPanel.vue'
import TagClusterPanel from '@/components/tag/advanced/TagClusterPanel.vue'
import TagNetworkPanel from '@/components/tag/advanced/TagNetworkPanel.vue'
import TagEffectPanel from '@/components/tag/advanced/TagEffectPanel.vue'
import TagTreePanel from '@/components/tag/advanced/TagTreePanel.vue'
import TagHistoryPanel from '@/components/tag/advanced/TagHistoryPanel.vue'
import TagBatchEditDrawer from '@/components/tag/advanced/TagBatchEditDrawer.vue'

const {
  activeTab,
  batchEditVisible,
  batchEditInitialTagIds,
  batchEditInitialCategory,
  openBatchEdit,
} = useTagAdvanced()

const PANELS = {
  health: TagHealthPanel,
  cluster: TagClusterPanel,
  network: TagNetworkPanel,
  effect: TagEffectPanel,
  tree: TagTreePanel,
  history: TagHistoryPanel,
} as const

/** 左侧导航中文标签（PANELS 的值是组件对象，不能直接渲染为文本） */
const PANEL_LABELS: Record<keyof typeof PANELS, string> = {
  health: '健康度',
  cluster: '聚类',
  network: '网络图',
  effect: '效果分析',
  tree: '层级树',
  history: '历史记录',
}

const currentPanel = computed(() => PANELS[activeTab.value])
</script>

<template>
  <div class="adv-page">
    <div class="page-header">
      <h2>标签高级管理</h2>
      <a-button type="primary" @click="openBatchEdit()">批量高级编辑</a-button>
    </div>

    <div class="adv-body">
      <!-- 左侧 Tab 导航 -->
      <aside class="adv-nav">
        <div
          v-for="(label, key) in PANEL_LABELS"
          :key="key"
          class="adv-nav-item"
          :class="{ active: activeTab === key }"
          @click="activeTab = key"
        >
          {{ label }}
        </div>
      </aside>

      <!-- 右侧工作区 -->
      <main class="adv-workspace">
        <KeepAlive>
          <component :is="currentPanel" />
        </KeepAlive>
      </main>
    </div>

    <!-- 全局批量编辑抽屉 -->
    <TagBatchEditDrawer
      v-model:visible="batchEditVisible"
      :initial-tag-ids="batchEditInitialTagIds"
      :initial-category="batchEditInitialCategory"
    />
  </div>
</template>

<style scoped>
.adv-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}
.page-header h2 {
  margin: 0;
}
.adv-body {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 12px;
}
/* 左侧 Tab 导航 */
.adv-nav {
  width: 120px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 8px 0;
  overflow-y: auto;
}
.adv-nav-item {
  padding: 10px 16px;
  cursor: pointer;
  font-size: 14px;
  color: #4b5563;
  border-left: 3px solid transparent;
  transition: all 0.15s;
}
.adv-nav-item:hover {
  background: #f3f4f6;
}
.adv-nav-item.active {
  color: #2a78d6;
  background: #eef4fd;
  border-left-color: #2a78d6;
  font-weight: 500;
}
/* 右侧工作区 */
.adv-workspace {
  flex: 1;
  min-width: 0;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  overflow-y: auto;
}
</style>
