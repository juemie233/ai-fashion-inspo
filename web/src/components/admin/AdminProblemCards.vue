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
    <a-card size="small" :bordered="true" style="border-color: #f0a020">
      <a-statistic v-if="stats" title="⚠️ 无标签素材" :value="stats.untagged_count" />
      <div v-else class="stat-custom">
        <span class="stat-custom-title">⚠️ 无标签素材</span>
        <span class="stat-custom-value">-</span>
      </div>
      <template #actions>
        <a-popconfirm
          content="确定删除所有无标签素材？此操作不可撤销。"
          @ok="emit('deleteUntagged')"
        >
          <a-button
            size="mini"
            type="outline"
            status="warning"
            :loading="clearingUntagged"
            :disabled="!stats?.untagged_count"
          >
            批量删除
          </a-button>
        </a-popconfirm>
      </template>
    </a-card>
    <a-card size="small" :bordered="true" style="border-color: #d03050">
      <a-statistic v-if="stats" title="❌ 分析失败素材" :value="stats.analysis_failed_count" />
      <div v-else class="stat-custom">
        <span class="stat-custom-title">❌ 分析失败素材</span>
        <span class="stat-custom-value">-</span>
      </div>
      <template #actions>
        <a-popconfirm
          content="确定删除所有分析失败的素材？此操作不可撤销。"
          @ok="emit('deleteFailed')"
        >
          <a-button
            size="mini"
            type="outline"
            status="danger"
            :loading="clearingFailed"
            :disabled="!stats?.analysis_failed_count"
          >
            批量删除
          </a-button>
        </a-popconfirm>
      </template>
    </a-card>
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

/* 自定义统计项（Arco Statistic 的 value 不接受字符串，空状态用文本展示） */
.stat-custom {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-custom-title {
  font-size: 14px;
  color: var(--color-text-2);
}

.stat-custom-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-1);
}
</style>
