<script setup lang="ts">
/** 素材库筛选栏：来源 / 媒体 / 状态 / 质量四组按钮筛选、标签与评分下拉、主色调色板，
 *  以及排序、瀑布流密度、素材打开方式三个控件。
 *
 * 从 HomeView.vue 抽出（视图只做编排）。筛选状态仍由视图侧的 useInspirationFilters()
 * 持有——通过 filters 参数整份传入（一组 ref + 选项 + setter），本组件不再重复定义
 * 筛选状态；筛选值变化后的「退出定位 + 回第一页 + 重新加载 + 同步 URL」由视图处理。
 */

import { computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useUiStore, type MaterialOpenMode } from '@/stores/ui'
// 只用它的返回类型，不在这里实例化（实例在视图里，状态必须唯一）
import { useInspirationFilters } from '@/composables/useInspirationFilters'

/** 筛选域返回值：状态 + 选项 + setter */
type Filters = ReturnType<typeof useInspirationFilters>
/** 瀑布流密度（与 MasonryGrid 的 density 取值一致） */
type Density = 'compact' | 'standard' | 'comfortable'

const props = defineProps<{
  /** 视图侧 useInspirationFilters() 的返回值 */
  filters: Filters
  /** 当前瀑布流密度 */
  density: Density
}>()

const emit = defineEmits<{
  /** 点密度按钮：视图负责写入 localStorage（偏好持久化） */
  (e: 'update:density', v: Density): void
  /** 标签 / 评分 / 排序下拉变更：视图负责退出定位、回第一页并重载 */
  (e: 'change'): void
}>()

// 解构出模板要用的名字（都是 ref / 函数，模板自动解包）
const {
  sourceFilter,
  mediaFilter,
  statusFilter,
  qualityFilter,
  sourceOptions,
  mediaOptions,
  statusOptions,
  qualityOptions,
  selectedTags,
  colorFilter,
  dominantColors,
  tagFilterOptions,
  ratingMin,
  ratingOptions,
  sortOptions,
  sortMode,
  setSourceFilter,
  setMediaFilter,
  setStatusFilter,
  setQualityFilter,
  setColorFilter,
} = props.filters

const densityOptions: { label: string; value: Density }[] = [
  { label: '紧凑', value: 'compact' },
  { label: '标准', value: 'standard' },
  { label: '宽松', value: 'comfortable' },
]

// ── 素材打开模式（全局偏好，直接读 ui store）──
const uiStore = useUiStore()
const materialOpenMode = computed({
  get: () => uiStore.materialOpenMode,
  set: (v: MaterialOpenMode) => uiStore.setMaterialOpenMode(v),
})

/** 切换提示：明确该设置的作用范围，避免误以为只影响本页 */
function onOpenModeChange() {
  Message.info(materialOpenMode.value === 'new_tab' ? '素材将在新标签页打开' : '素材将在当前页打开')
}
</script>

<template>
  <!-- 筛选栏 -->
  <div class="filter-bar">
    <!-- 来源筛选 -->
    <div class="filter-group">
      <a-button
        v-for="opt in sourceOptions"
        :key="opt.value"
        size="mini"
        :type="sourceFilter === opt.value ? 'primary' : 'secondary'"
        @click="setSourceFilter(opt.value)"
      >
        {{ opt.label }}
      </a-button>
    </div>

    <a-divider direction="vertical" style="height: 20px" />

    <!-- 媒体筛选 -->
    <div class="filter-group">
      <a-button
        v-for="opt in mediaOptions"
        :key="opt.value"
        size="mini"
        :type="mediaFilter === opt.value ? 'primary' : 'secondary'"
        @click="setMediaFilter(opt.value)"
      >
        {{ opt.label }}
      </a-button>
    </div>

    <a-divider direction="vertical" style="height: 20px" />

    <!-- 状态筛选 -->
    <div class="filter-group">
      <a-button
        v-for="opt in statusOptions"
        :key="opt.value"
        size="mini"
        :type="statusFilter === opt.value ? 'primary' : 'secondary'"
        @click="setStatusFilter(opt.value)"
      >
        {{ opt.label }}
      </a-button>
    </div>

    <a-divider direction="vertical" style="height: 20px" />

    <!-- 质量审核筛选 -->
    <div class="filter-group">
      <a-button
        v-for="opt in qualityOptions"
        :key="opt.value"
        size="mini"
        :type="qualityFilter === opt.value ? 'primary' : 'secondary'"
        @click="setQualityFilter(opt.value)"
      >
        {{ opt.label }}
      </a-button>
    </div>

    <a-divider direction="vertical" style="height: 20px" />

    <!-- 标签筛选（多选，需同时包含） -->
    <div class="filter-group">
      <a-select
        v-model="selectedTags"
        multiple
        filterable
        allow-clear
        :options="tagFilterOptions"
        placeholder="按标签筛选"
        size="mini"
        style="width: 220px"
        @change="emit('change')"
      />
    </div>

    <a-divider direction="vertical" style="height: 20px" />

    <!-- 颜色筛选（主色调色板，数据驱动） -->
    <div v-if="dominantColors.length > 0" class="filter-group color-filter" title="按主色调筛选">
      <span
        v-for="c in dominantColors"
        :key="c.color"
        class="color-swatch"
        :class="{ active: colorFilter === c.color }"
        :style="{ background: c.color }"
        :title="`${c.color}（${c.count} 个素材）`"
        @click="setColorFilter(colorFilter === c.color ? '' : c.color)"
      />
      <a-button v-if="colorFilter" size="mini" type="text" @click="setColorFilter('')">
        清除颜色
      </a-button>
    </div>

    <a-divider v-if="dominantColors.length > 0" direction="vertical" style="height: 20px" />

    <!-- 评分筛选（评分 >= 指定值） -->
    <div class="filter-group">
      <a-select
        v-model="ratingMin"
        :options="ratingOptions"
        placeholder="评分筛选"
        size="mini"
        style="width: 130px"
        @change="emit('change')"
      />
    </div>

    <div style="flex: 1" />

    <!-- 排序 + 密度 -->
    <div class="control-group">
      <a-select
        v-model="sortMode"
        :options="sortOptions"
        size="mini"
        style="width: 110px"
        @change="emit('change')"
      />

      <a-button-group size="mini">
        <a-button
          v-for="d in densityOptions"
          :key="d.value"
          :type="density === d.value ? 'primary' : 'secondary'"
          @click="emit('update:density', d.value)"
        >
          {{ d.label }}
        </a-button>
      </a-button-group>

      <!-- 素材打开模式：全局生效（素材库/搜索/合集/相似推荐等所有素材卡片） -->
      <a-tooltip
        content="点击素材卡片时的打开方式，全局生效（素材库、搜索、收藏合集、相似推荐等所有素材入口）；偏好自动保存"
      >
        <a-radio-group
          v-model="materialOpenMode"
          type="button"
          size="mini"
          @change="onOpenModeChange"
        >
          <a-radio value="new_tab">新页打开</a-radio>
          <a-radio value="current_tab">当前页打开</a-radio>
        </a-radio-group>
      </a-tooltip>
    </div>
  </div>
</template>

<style scoped>
/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f8f9fa;
  border-radius: 8px;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  gap: 4px;
}

/* 颜色筛选色板 */
.color-filter {
  align-items: center;
}
.color-swatch {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid transparent;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.08);
  transition:
    transform 0.1s,
    border-color 0.1s;
}
.color-swatch:hover {
  transform: scale(1.15);
}
.color-swatch.active {
  border-color: #2080f0;
}
.color-chip {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: middle;
}

.control-group {
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>
