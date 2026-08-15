<script setup lang="ts">
/** 高级搜索筛选面板：标签筛选 + 更多筛选（来源/媒体/分析状态/日期） + 搜索按钮。 */

import { computed } from 'vue'
import TagFilter from './TagFilter.vue'

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
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'update:sourceFilter', value: string): void
  (e: 'update:mediaFilter', value: string): void
  (e: 'update:analysisFilter', value: string): void
  (e: 'update:dateFrom', value: string): void
  (e: 'update:dateTo', value: string): void
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

/** 开始日期（n-input 的 change 事件在失焦/回车时触发搜索） */
const dateFromModel = computed({
  get: () => props.dateFrom,
  set: (v: string) => emit('update:dateFrom', v),
})

/** 结束日期 */
const dateToModel = computed({
  get: () => props.dateTo,
  set: (v: string) => emit('update:dateTo', v),
})
</script>

<template>
  <transition name="slide">
    <aside v-if="visible" class="filter-panel">
      <n-card title="标签筛选" size="small" :bordered="true">
        <template #header-extra>
          <n-button size="tiny" text @click="emit('update:visible', false)">收起 ✕</n-button>
        </template>
        <TagFilter />

        <!-- 更多筛选（可折叠） -->
        <n-collapse style="margin-top:8px">
          <n-collapse-item title="更多筛选" name="more">
            <div class="more-filters">
              <div class="filter-row">
                <label>来源</label>
                <n-select
                  v-model:value="sourceFilterModel"
                  :options="[
                    {label:'全部',value:''},{label:'手动上传',value:'manual_upload'},
                    {label:'自动采集',value:'scraper'},{label:'小红书',value:'xiaohongshu'},
                    {label:'抖音',value:'douyin'},{label:'浏览器插件',value:'browser_extension'}
                  ]"
                  size="tiny"
                  clearable
                />
              </div>
              <div class="filter-row">
                <label>媒体</label>
                <n-select
                  v-model:value="mediaFilterModel"
                  :options="[{label:'全部',value:''},{label:'图片',value:'image'},{label:'视频',value:'video'}]"
                  size="tiny"
                />
              </div>
              <div class="filter-row">
                <label>分析状态</label>
                <n-select
                  v-model:value="analysisFilterModel"
                  :options="[{label:'全部',value:''},{label:'已分析',value:'done'},{label:'未分析',value:'pending'},{label:'分析失败',value:'error'}]"
                  size="tiny"
                />
              </div>
              <div class="filter-row">
                <label>开始日期</label>
                <n-input v-model:value="dateFromModel" type="date" size="tiny" @change="emit('filterChange')" />
              </div>
              <div class="filter-row">
                <label>结束日期</label>
                <n-input v-model:value="dateToModel" type="date" size="tiny" @change="emit('filterChange')" />
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>

        <n-button
          type="primary"
          block
          style="margin-top:8px"
          :loading="searching"
          @click="emit('search')"
        >
          搜索
        </n-button>
      </n-card>
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

/* 折叠动画 */
.slide-enter-active, .slide-leave-active {
  transition: width 0.25s ease, opacity 0.2s ease;
  overflow: hidden;
}
.slide-enter-from, .slide-leave-to {
  width: 0;
  opacity: 0;
}

@media (max-width: 900px) {
  .filter-panel { width: 100%; }
}
</style>
