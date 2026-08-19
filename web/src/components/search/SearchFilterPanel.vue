<script setup lang="ts">
/** 高级搜索筛选面板：标签筛选 + 更多筛选（来源/媒体/分析状态/日期） + 搜索按钮。 */

import { computed } from 'vue'
import TagFilter from './TagFilter.vue'
import { buildSourceOptions } from '@/utils/sourceLabel'

/** 来源筛选选项（由 sourceLabel.ts 统一生成） */
const sourceFilterOptions = buildSourceOptions('', '全部')

const props = defineProps<{
  /** 面板是否可见 */
  visible: boolean
  /** 搜索执行中 */
  searching: boolean
  /** 来源筛选值 */
  sourceFilter: string
  /** 媒体筛选值 */
  mediaFilter: string
  /** 分析状态筛选值 */
  analysisFilter: string
  /** 开始日期 */
  dateFrom: string
  /** 结束日期 */
  dateTo: string
  /** 评分筛选值（rating >= 指定值，空串表示不限） */
  ratingFilter: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'update:sourceFilter', value: string): void
  (e: 'update:mediaFilter', value: string): void
  (e: 'update:analysisFilter', value: string): void
  (e: 'update:dateFrom', value: string): void
  (e: 'update:dateTo', value: string): void
  (e: 'update:ratingFilter', value: string): void
  /** 任一筛选值变化（由用户操作触发，用于立即重新搜索） */
  (e: 'filterChange'): void
  /** 点击搜索按钮 */
  (e: 'search'): void
}>()

/** 来源筛选（变更即触发重新搜索） */
const sourceFilterModel = computed({
  get: () => props.sourceFilter,
  set: (v: string) => {
    emit('update:sourceFilter', v)
    emit('filterChange')
  },
})

/** 媒体筛选（变更即触发重新搜索） */
const mediaFilterModel = computed({
  get: () => props.mediaFilter,
  set: (v: string) => {
    emit('update:mediaFilter', v)
    emit('filterChange')
  },
})

/** 分析状态筛选（变更即触发重新搜索） */
const analysisFilterModel = computed({
  get: () => props.analysisFilter,
  set: (v: string) => {
    emit('update:analysisFilter', v)
    emit('filterChange')
  },
})

/** 开始日期（a-date-picker 的 change 事件在选中/清空日期时触发搜索） */
const dateFromModel = computed({
  get: () => props.dateFrom,
  set: (v: string | undefined) => emit('update:dateFrom', v ?? ''),
})

/** 结束日期 */
const dateToModel = computed({
  get: () => props.dateTo,
  set: (v: string | undefined) => emit('update:dateTo', v ?? ''),
})

/** 评分筛选（变更即触发重新搜索） */
const ratingFilterModel = computed({
  get: () => props.ratingFilter,
  set: (v: string) => {
    emit('update:ratingFilter', v)
    emit('filterChange')
  },
})
</script>

<template>
  <transition name="slide">
    <aside v-if="visible" class="filter-panel">
      <a-card title="标签筛选" size="small" :bordered="true">
        <template #extra>
          <a-button size="mini" type="text" @click="emit('update:visible', false)">收起 ✕</a-button>
        </template>
        <TagFilter />

        <!-- 更多筛选（可折叠） -->
        <a-collapse style="margin-top: 8px">
          <a-collapse-item header="更多筛选" key="more">
            <div class="more-filters">
              <div class="filter-row">
                <label>来源</label>
                <a-select
                  v-model="sourceFilterModel"
                  :options="sourceFilterOptions"
                  size="mini"
                  allow-clear
                />
              </div>
              <div class="filter-row">
                <label>媒体</label>
                <a-select
                  v-model="mediaFilterModel"
                  :options="[
                    { label: '全部', value: '' },
                    { label: '图片', value: 'image' },
                    { label: '视频', value: 'video' },
                  ]"
                  size="mini"
                />
              </div>
              <div class="filter-row">
                <label>分析状态</label>
                <a-select
                  v-model="analysisFilterModel"
                  :options="[
                    { label: '全部', value: '' },
                    { label: '已分析', value: 'done' },
                    { label: '未分析', value: 'pending' },
                    { label: '分析失败', value: 'error' },
                  ]"
                  size="mini"
                />
              </div>
              <div class="filter-row">
                <label>开始日期</label>
                <a-date-picker
                  v-model="dateFromModel"
                  value-format="YYYY-MM-DD"
                  size="mini"
                  allow-clear
                  @change="emit('filterChange')"
                />
              </div>
              <div class="filter-row">
                <label>结束日期</label>
                <a-date-picker
                  v-model="dateToModel"
                  value-format="YYYY-MM-DD"
                  size="mini"
                  allow-clear
                  @change="emit('filterChange')"
                />
              </div>
              <div class="filter-row">
                <label>评分</label>
                <a-select
                  v-model="ratingFilterModel"
                  :options="[
                    { label: '全部', value: '' },
                    { label: '★ 1 分及以上', value: '1' },
                    { label: '★ 2 分及以上', value: '2' },
                    { label: '★ 3 分及以上', value: '3' },
                    { label: '★ 4 分及以上', value: '4' },
                    { label: '★ 5 分', value: '5' },
                  ]"
                  size="mini"
                />
              </div>
            </div>
          </a-collapse-item>
        </a-collapse>

        <a-button
          type="primary"
          long
          style="margin-top: 8px"
          :loading="searching"
          @click="emit('search')"
        >
          搜索
        </a-button>
      </a-card>
    </aside>
  </transition>
</template>

<style scoped>
.filter-panel {
  width: 290px;
  flex-shrink: 0;
}

/* 更多筛选 */
.more-filters .filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.more-filters label {
  font-size: 12px;
  color: #666;
  width: 60px;
  flex-shrink: 0;
}

/* Arco 控件默认自适应宽度：让下拉/日期选择在筛选行内撑满剩余空间（等价 Naive 默认 100% 宽） */
.more-filters .filter-row :deep(.arco-select),
.more-filters .filter-row :deep(.arco-picker) {
  flex: 1;
  min-width: 0;
}

/* 折叠动画 */
.slide-enter-active,
.slide-leave-active {
  transition:
    width 0.25s ease,
    opacity 0.2s ease;
  overflow: hidden;
}
.slide-enter-from,
.slide-leave-to {
  width: 0;
  opacity: 0;
}

@media (max-width: 900px) {
  .filter-panel {
    width: 100%;
  }
}
</style>
