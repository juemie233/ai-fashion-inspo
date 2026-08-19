<script setup lang="ts">
/** AI 模型管理页：5 个功能面板的 tab 容器。 */

import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAiModelsStore } from '@/stores/aiModels'
import ModelListPanel from '@/components/model/ModelListPanel.vue'
import AnalysisPanel from '@/components/model/AnalysisPanel.vue'
import SettingsPanel from '@/components/model/SettingsPanel.vue'
import QualityPanel from '@/components/model/QualityPanel.vue'
import ReviewPanel from '@/components/model/ReviewPanel.vue'

const store = useAiModelsStore()
const route = useRoute()
const router = useRouter()

// 从 URL query 恢复当前 tab，刷新后不再回到第一个页面
const VALID_TABS = ['models', 'queue', 'settings', 'quality', 'review']
const initialTab = route.query.tab as string
const activeTab = ref(VALID_TABS.includes(initialTab) ? initialTab : 'models')

// tab 变更时同步到 URL，刷新/分享链接可恢复
watch(activeTab, (tab) => {
  const query = { ...route.query }
  if (tab === 'models') delete query.tab
  else query.tab = tab
  router.replace({ query })
})

onMounted(() => {
  store.refreshModels()
})
</script>

<template>
  <div class="model-page">
    <h2>AI 模型管理</h2>

    <a-tabs v-model:active-key="activeTab" type="line">
      <a-tab-pane key="models" title="模型管理">
        <ModelListPanel />
      </n-tab-pane>
      <a-tab-pane key="review" title="质量审核">
        <ReviewPanel />
      </n-tab-pane>
      <a-tab-pane key="queue" title="标签分析">
        <AnalysisPanel />
      </n-tab-pane>
      <a-tab-pane key="settings" title="参数调优">
        <SettingsPanel />
      </n-tab-pane>
      <a-tab-pane key="quality" title="分析质量">
        <QualityPanel />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.model-page { max-width: 1100px; margin: 0 auto; }
.model-page h2 { margin-bottom: 16px; }
</style>
