<script setup lang="ts">
/** 问题概览卡片：无标签素材 / 分析失败素材的批量删除入口。 */

import type { Stats } from '@/types/admin'

defineProps<{
  stats: Stats | null
  clearingUntagged: boolean
  clearingFailed: boolean
}>()

const emit = defineEmits<{
  (e: 'deleteUntagged'): void
  (e: 'deleteFailed'): void
}>()
</script>

<template>
  <div class="stat-cards" style="margin-top: 16px">
    <n-card size="small" :bordered="true" style="border-color: #f0a020">
      <n-statistic label="⚠️ 无标签素材" :value="stats?.untagged_count ?? '-'" />
      <template #footer>
        <n-popconfirm @positive-click="emit('deleteUntagged')">
          <template #trigger>
            <n-button size="tiny" type="warning" ghost :loading="clearingUntagged"
              :disabled="!stats?.untagged_count">
              批量删除
            </n-button>
          </template>
          确定删除所有无标签素材？此操作不可撤销。
        </n-popconfirm>
      </template>
    </n-card>
    <n-card size="small" :bordered="true" style="border-color: #d03050">
      <n-statistic label="❌ 分析失败素材" :value="stats?.analysis_failed_count ?? '-'" />
      <template #footer>
        <n-popconfirm @positive-click="emit('deleteFailed')">
          <template #trigger>
            <n-button size="tiny" type="error" ghost :loading="clearingFailed"
              :disabled="!stats?.analysis_failed_count">
              批量删除
            </n-button>
          </template>
          确定删除所有分析失败的素材？此操作不可撤销。
        </n-popconfirm>
      </template>
    </n-card>
  </div>
</template>

<style scoped>
/* 统计卡片网格 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
</style>
