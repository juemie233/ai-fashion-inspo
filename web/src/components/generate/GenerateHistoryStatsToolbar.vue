<script setup lang="ts">
/** 生成历史顶部统计卡 + 筛选工具栏（从 GenerateHistoryTable 抽出）。
 *
 * 只做展示与回抛筛选变更；筛选状态、日期换算、导出与对比逻辑仍由父组件持有。
 */

import type { GenerationHistoryQuery, GenerationRunStats } from '@/api/generate'

const props = defineProps<{
  stats: GenerationRunStats | null
  importRateText: string
  failureRateText: string
  topModels: GenerationRunStats['by_model']
  maxModelRuns: number
  modelLabel: (key: string) => string
  filters: GenerationHistoryQuery
  modelOptions: Array<{ label: string; value: string }>
  statusOptions: Array<{ label: string; value: string }>
  dateRange?: [string, string]
  keyword: string
  filtered: boolean
  canCompare: boolean
  compareCount: number
  total: number
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'update:filters', filters: GenerationHistoryQuery): void
  (e: 'date-change', value: [string, string] | null): void
  (e: 'update:keyword', value: string): void
  (e: 'export', fmt: 'csv' | 'json'): void
  (e: 'compare'): void
}>()

function patchFilters(patch: Partial<GenerationHistoryQuery>): void {
  emit('update:filters', { ...props.filters, ...patch })
}
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成历史</span>
    <span class="gen-history-hint">点行首箭头看完整描述、参数与全部候选图</span>
    <a-button size="mini" :loading="loading" @click="emit('refresh')">刷新</a-button>
  </div>

  <!-- 统计卡（近 30 天）：入库率按「张」算，耗时含排队等待 -->
  <div v-if="stats" class="gh-stats">
    <div class="gh-stat">
      <b>{{ stats.runs }}</b
      ><span>批次</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.images }}</b
      ><span>出图</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.imported_images }}</b
      ><span>入库（{{ importRateText }}）</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.avg_duration_s }}s</b><span>均耗时</span>
    </div>
    <div class="gh-stat">
      <b>{{ failureRateText }}</b
      ><span>失败率</span>
    </div>
    <div v-if="topModels.length" class="gh-stat-models">
      <div v-for="item in topModels" :key="item.model_key" class="gh-stat-model">
        <span class="gh-stat-model-name">{{ modelLabel(item.model_key) }}</span>
        <span class="gh-stat-model-bar">
          <i :style="{ width: `${Math.round((item.runs / maxModelRuns) * 100)}%` }" />
        </span>
        <span class="gh-stat-model-num">{{ item.runs }} 批</span>
      </div>
    </div>
  </div>

  <!-- 筛选工具栏：全部走后端参数（total 与 items 同口径，分页才不会飘） -->
  <div class="gh-toolbar">
    <a-select
      :model-value="filters.model_key || ''"
      :options="modelOptions"
      size="mini"
      style="width: 170px"
      @update:model-value="patchFilters({ model_key: String($event) || undefined })"
    />
    <a-select
      :model-value="filters.status || ''"
      :options="statusOptions"
      size="mini"
      style="width: 120px"
      @update:model-value="patchFilters({ status: String($event) || undefined })"
    />
    <a-checkbox
      :model-value="Boolean(filters.has_imported)"
      @update:model-value="patchFilters({ has_imported: Boolean($event) || undefined })"
    >
      只看已入库
    </a-checkbox>
    <a-range-picker
      :model-value="dateRange"
      size="mini"
      style="width: 216px"
      value-format="YYYY-MM-DD"
      @change="(value: unknown) => emit('date-change', value as [string, string] | null)"
    />
    <a-input
      :model-value="keyword"
      size="mini"
      placeholder="搜描述 / 负面词"
      allow-clear
      style="width: 180px"
      @update:model-value="(v: string) => emit('update:keyword', v)"
    />
    <a-button v-if="filtered" size="mini" type="text" @click="emit('update:filters', {})">
      清空筛选
    </a-button>
    <!-- 参考素材筛选的可见反馈（从展开行点参考图进来） -->
    <a-tag
      v-if="filters.reference_id"
      size="small"
      closable
      @close="patchFilters({ reference_id: undefined })"
    >
      只看这张参考图（{{ String(filters.reference_id).slice(0, 8) }}…）
    </a-tag>
    <!-- 导出当前筛选结果（C2）：走接口拿真文件名，且带上筛选条件 -->
    <a-dropdown>
      <a-button size="mini" style="margin-left: auto">导出</a-button>
      <template #content>
        <a-doption @click="emit('export', 'csv')">导出 CSV（Excel 可直接打开）</a-doption>
        <a-doption @click="emit('export', 'json')">导出 JSON</a-doption>
      </template>
    </a-dropdown>
    <a-button size="mini" type="primary" :disabled="!canCompare" @click="emit('compare')">
      对比这两批{{ compareCount ? `（${compareCount}/2）` : '' }}
    </a-button>
    <span class="gen-history-hint">共 {{ total }} 条</span>
  </div>
</template>

<style scoped>
.gh-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 18px;
  margin: 8px 0 10px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #f7f8fa;
}

.gh-stat {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  color: #86909c;
}

.gh-stat b {
  font-size: 16px;
  color: #1d2129;
}

.gh-stat-models {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 240px;
}

.gh-stat-model {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #4e5969;
}

.gh-stat-model-name {
  width: 110px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.gh-stat-model-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #e5e6eb;
  overflow: hidden;
}

.gh-stat-model-bar i {
  display: block;
  height: 100%;
  background: rgb(var(--primary-5));
}
</style>
