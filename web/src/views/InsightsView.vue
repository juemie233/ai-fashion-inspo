<script setup lang="ts">
/** 数据洞察页：向量管理 + 数据报表（导出/趋势/人物频次/审计日志）。 */

import { ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'

import AdminVectorPanel from '@/components/admin/AdminVectorPanel.vue'
import AdminExportPanel from '@/components/admin/AdminExportPanel.vue'
import AdminTrendChart from '@/components/admin/AdminTrendChart.vue'
import AdminPersonFrequency from '@/components/admin/AdminPersonFrequency.vue'
import AdminAuditLog from '@/components/admin/AdminAuditLog.vue'

const router = useRouter()
const route = useRoute()

// ── 子页面（小菜单）状态：URL 持久化，刷新后停留在原小页面 ──
type InsightsTab = 'vectors' | 'reports'
const INSIGHTS_TABS: InsightsTab[] = ['vectors', 'reports']

function initialTab(): InsightsTab {
  const t = route.query.tab
  return t && INSIGHTS_TABS.includes(t as InsightsTab) ? (t as InsightsTab) : 'reports'
}
const activeTab = ref<InsightsTab>(initialTab())

watch(activeTab, (tab) => {
  const query = { ...route.query }
  if (tab === 'reports') {
    delete query.tab
  } else {
    query.tab = tab
  }
  router.replace({ query })
})
</script>

<template>
  <div class="insights-page">
    <h2>数据洞察</h2>
    <p class="subtitle">向量状态、导出与统计报表</p>

    <a-tabs v-model:active-key="activeTab" type="line">
      <!-- 向量管理 -->
      <a-tab-pane key="vectors" title="向量管理">
        <admin-vector-panel />
      </a-tab-pane>

      <!-- 数据报表 -->
      <a-tab-pane key="reports" title="数据报表">
        <div class="reports-layout">
          <admin-export-panel />
          <admin-trend-chart />
          <admin-person-frequency />
          <admin-audit-log class="reports-full" />
        </div>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.insights-page {
  max-width: 1100px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 24px;
}

.reports-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.reports-layout > :first-child {
  grid-column: 1 / -1;
}
.reports-layout :deep(.reports-full) {
  grid-column: 1 / -1;
}

@media (max-width: 900px) {
  .reports-layout {
    grid-template-columns: 1fr;
  }
}
</style>
