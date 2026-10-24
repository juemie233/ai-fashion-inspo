<script setup lang="ts">
/** 标签详情小卡：使用趋势 + 常一起出现的标签。
 *
 * 判断「这个标签值不值得留 / 该不该合并」需要两个信号：它自己的走势（是不是已经被
 * 更好的词替代）和它总跟谁一起出现（同义/近义往往共现极高）。点共现标签可跳转过去。
 * 数据在 useTagGovernance.useTagInsight。
 */

import { toRef } from 'vue'
import type { TagItem } from '@/api/tags'
import { useTagInsight } from '@/composables/useTagGovernance'

const props = defineProps<{
  /** 当前选中的标签（null = 未选择，不渲染） */
  tag: TagItem | null
}>()

const emit = defineEmits<{ select: [tagId: number] }>()

const { loading, cooccurrence, materials, recentTrend, maxCount, follow } = useTagInsight()

// 跟随选中标签自动加载（切换标签即刷新，清空则清屏）
follow(toRef(() => props.tag))
</script>

<template>
  <div v-if="props.tag" class="insight">
    <div class="head">
      <span class="title">「{{ props.tag.name }}」洞察</span>
      <span class="meta">
        当前关联 {{ materials }} 个素材 · 累计用量 {{ props.tag.usage_count }}
      </span>
    </div>

    <a-spin :loading="loading" style="display: block">
      <!-- 趋势：纯 div 柱状，不引图表库 -->
      <div class="section">
        <div class="section-title">最近 6 个月使用量</div>
        <div v-if="recentTrend.length === 0" class="empty">还没有使用记录</div>
        <div v-else class="bars">
          <div v-for="point in recentTrend" :key="point.bucket" class="bar-wrap">
            <div
              class="bar"
              :style="{ height: `${Math.max(4, (point.count / maxCount) * 48)}px` }"
              :title="`${point.bucket}: ${point.count}`"
            />
            <div class="bar-count">{{ point.count }}</div>
            <div class="bar-label">{{ point.bucket.replace(/^\d{4}-/, '') }}</div>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">常一起出现的标签</div>
        <div v-if="cooccurrence.length === 0" class="empty">共现数据不足（该标签的素材还太少）</div>
        <div v-else class="chips">
          <a-tag
            v-for="item in cooccurrence"
            :key="item.id"
            class="chip"
            size="small"
            @click="emit('select', item.id)"
          >
            {{ item.name }}
            <span class="chip-count">×{{ item.count }}</span>
          </a-tag>
        </div>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.insight {
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  background: var(--color-fill-1);
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.title {
  font-weight: 600;
}

.meta {
  color: var(--color-text-3);
  font-size: 12px;
}

.section {
  margin-top: 8px;
}

.section-title {
  font-size: 12px;
  color: var(--color-text-3);
  margin-bottom: 6px;
}

.bars {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  height: 84px;
}

.bar-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.bar {
  width: 18px;
  border-radius: 3px 3px 0 0;
  background: var(--color-primary-light-3, #6aa1e8);
}

.bar-count {
  font-size: 11px;
  color: var(--color-text-2);
}

.bar-label {
  font-size: 11px;
  color: var(--color-text-4);
  white-space: nowrap;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  cursor: pointer;
}

.chip-count {
  margin-left: 4px;
  color: var(--color-text-3);
}

.empty {
  font-size: 12px;
  color: var(--color-text-3);
}
</style>
