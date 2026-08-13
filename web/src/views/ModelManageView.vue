<script setup lang="ts">
/** AI 模型管理页：5 个功能面板的 tab 容器。 */

import { ref, onMounted } from 'vue'
import { useAiModelsStore } from '@/stores/aiModels'
import ModelListPanel from '@/components/model/ModelListPanel.vue'
import AnalysisPanel from '@/components/model/AnalysisPanel.vue'
import SettingsPanel from '@/components/model/SettingsPanel.vue'
import QualityPanel from '@/components/model/QualityPanel.vue'
import ReviewPanel from '@/components/model/ReviewPanel.vue'

const store = useAiModelsStore()
const activeTab = ref('models')

onMounted(() => {
  store.refreshModels()
})
</script>

<template>
  <div class="model-page">
    <h2>AI 模型管理</h2>

    <n-tabs v-model:value="activeTab" type="line">
      <n-tab-pane name="models" tab="模型管理">
        <ModelListPanel />
      </n-tab-pane>
      <n-tab-pane name="queue" tab="标签分析">
        <AnalysisPanel />
      </n-tab-pane>
      <n-tab-pane name="settings" tab="参数调优">
        <SettingsPanel />
      </n-tab-pane>
      <n-tab-pane name="quality" tab="分析质量">
        <QualityPanel />
      </n-tab-pane>
      <n-tab-pane name="review" tab="质量审核">
        <ReviewPanel />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.model-page { max-width: 1100px; margin: 0 auto; }
.model-page h2 { margin-bottom: 16px; }
</style>
