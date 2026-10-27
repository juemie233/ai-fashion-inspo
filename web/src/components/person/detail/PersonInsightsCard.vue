<script setup lang="ts">
/** 人物洞察卡（B3 活跃度 + B4 质量构成）：这号还更新吗？素材值不值得留？
 *
 * 两个问题在详情页是同一个决策（要不要继续追、要不要清理），所以放同一张卡：
 * - 近 12 个月「入库新增」柱状 + 最近/首次入库时间 + 断更标记；
 * - 质量三段条（通过/待审/拒绝），点一段可跳到该质量的素材筛选。
 */

import { computed } from 'vue'
import type { PersonInsights } from '@/api/persons'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  insights: PersonInsights | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'filter-quality', status: 'approved' | 'pending' | 'rejected'): void
}>()

/** 柱状图按最大值归一（空数据时统一 0 高，不画假柱子） */
const maxMonthly = computed(() =>
  Math.max(1, ...(props.insights?.monthly ?? []).map((m) => m.count)),
)

const total = computed(() => props.insights?.total ?? 0)

/** 质量三段（unreviewed 不计入分母：那是「还没跑审核」，不是一种结果） */
const qualitySegments = computed(() => {
  const q = props.insights?.quality
  if (!q) return []
  const judged = q.approved + q.pending + q.rejected
  return [
    { key: 'approved' as const, label: '通过', count: q.approved, color: '#00b42a' },
    { key: 'pending' as const, label: '待审', count: q.pending, color: '#ff7d00' },
    { key: 'rejected' as const, label: '拒绝', count: q.rejected, color: '#f53f3f' },
  ].map((s) => ({
    ...s,
    percent: judged ? Math.round((s.count / judged) * 100) : 0,
  }))
})

/** 断更判断：最近一次入库超过 90 天（作品可以老，但素材迟迟没新增说明采集停了） */
const dormant = computed(() => {
  const last = props.insights?.last_added
  if (!last) return false
  return Date.now() - new Date(last).getTime() > 90 * 24 * 3600 * 1000
})

const mediaText = computed(() => {
  const media = props.insights?.media ?? {}
  const parts = Object.entries(media).map(([k, v]) => `${k === 'video' ? '视频' : '图片'} ${v}`)
  return parts.join(' · ')
})
</script>

<template>
  <a-card size="small" class="insight-card" :loading="loading">
    <template #title>
      活跃度与质量
      <a-tag v-if="dormant" size="small" color="gray" class="dormant-tag">超过 90 天无新增</a-tag>
    </template>
    <template #extra>
      <span class="hint">{{ mediaText }}</span>
    </template>

    <div class="insight-row">
      <div class="insight-block">
        <div class="block-title">近 12 个月入库新增</div>
        <div class="bars">
          <div
            v-for="m in insights?.monthly ?? []"
            :key="m.month"
            class="bar-col"
            :title="`${m.month}：${m.count} 条`"
          >
            <div class="bar" :style="{ height: `${Math.round((m.count / maxMonthly) * 100)}%` }" />
            <span class="bar-label">{{ m.month.slice(5) }}</span>
          </div>
        </div>
        <div class="hint">
          首次入库 {{ insights?.first_added ? formatDateTime(insights.first_added) : '-' }} ·
          最近入库 {{ insights?.last_added ? formatDateTime(insights.last_added) : '-' }}
        </div>
      </div>

      <div class="insight-block insight-quality">
        <div class="block-title">质量构成（共 {{ total }} 条素材）</div>
        <div class="quality-bar">
          <div
            v-for="seg in qualitySegments"
            :key="seg.key"
            class="quality-seg"
            :style="{ width: `${seg.percent}%`, background: seg.color }"
            :title="`${seg.label} ${seg.count} 条`"
            @click="seg.count > 0 && emit('filter-quality', seg.key)"
          />
        </div>
        <div class="quality-legend">
          <span
            v-for="seg in qualitySegments"
            :key="seg.key"
            class="legend-item"
            :class="{ 'is-clickable': seg.count > 0 }"
            @click="seg.count > 0 && emit('filter-quality', seg.key)"
          >
            <i :style="{ background: seg.color }" />
            {{ seg.label }} {{ seg.count }}（{{ seg.percent }}%）
          </span>
        </div>
        <div class="hint">点某一段可直接筛选该状态的素材</div>
      </div>
    </div>
  </a-card>
</template>

<style scoped>
.insight-card {
  margin-bottom: 16px;
}
.dormant-tag {
  margin-left: 8px;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
  line-height: 1.7;
}
.insight-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 20px;
}
@media (max-width: 900px) {
  .insight-row {
    grid-template-columns: 1fr;
  }
}
.block-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 96px;
  padding-bottom: 2px;
  border-bottom: 1px solid var(--color-border-2);
}
.bar-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  height: 100%;
}
.bar {
  width: 100%;
  max-width: 26px;
  min-height: 1px;
  background: rgb(var(--primary-5));
  border-radius: 2px 2px 0 0;
}
.bar-label {
  font-size: 10px;
  color: var(--color-text-3);
  margin-top: 2px;
  white-space: nowrap;
}
.quality-bar {
  display: flex;
  height: 14px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--color-fill-3);
}
.quality-seg {
  height: 100%;
  cursor: pointer;
}
.quality-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-2);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.legend-item.is-clickable {
  cursor: pointer;
}
.legend-item i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  display: inline-block;
}
</style>
