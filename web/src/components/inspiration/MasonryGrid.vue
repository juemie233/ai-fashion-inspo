<script setup lang="ts">
/** 瀑布流网格：自适应列数，展示素材卡片列表。
 *
 * 采用「行优先」分列（第 i 个素材放入第 i % 列数 的列），保证视觉顺序
 * 按行从左到右、从上到下，与「最新在前」等时间排序的扫视习惯一致；
 * 列数随密度与视口宽度响应式调整。 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import InspirationCard from './InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

const props = defineProps<{
  items: InspirationOut[]
  loading?: boolean
  density?: 'compact' | 'standard' | 'comfortable'
  /** 卡片角标映射：素材 id -> 角标文本（如「92% 相似」），用于向量搜索结果 */
  badges?: Record<string, string>
  /** 批量选择模式：显示勾选框，点击卡片切换勾选而非跳转详情 */
  selectable?: boolean
  /** 已勾选的素材 id 集合（选择模式下生效） */
  selectedIds?: ReadonlySet<string>
  /** 空状态提示文案 */
  emptyText?: string
  /** 是否显示悬浮操作按钮（删除/收藏），默认显示 */
  showActions?: boolean
  /** 悬停放大预览：鼠标停留在素材上超过 2 秒时弹出大图（疑似 AI 页面启用） */
  hoverZoom?: boolean
  /** 显示「浏览详情」按钮：选择模式下点击卡片只能勾选，需单独提供入口跳转详情页 */
  showViewButton?: boolean
  /** 定位模式：这些素材 id 高亮（金色描边），列表加载后自动滚动到首个目标 */
  focusedIds?: string[]
}>()

const emit = defineEmits<{
  (e: 'delete', id: string): void
  (e: 'toggleFavorite', id: string): void
  (e: 'rate', id: string, value: number): void
  (e: 'approve', id: string): void
  (e: 'toggleSelect', id: string): void
}>()

// ── 响应式列数：断点与原 CSS columns 行为保持一致 ──

/** 密度 -> (视口宽度上限, 列数) 列表，从窄到宽 */
const DENSITY_BREAKPOINTS: Record<string, Array<{ max: number; cols: number }>> = {
  compact: [
    { max: 700, cols: 2 },
    { max: 1000, cols: 3 },
    { max: 1400, cols: 4 },
    { max: Infinity, cols: 6 },
  ],
  standard: [
    { max: 700, cols: 1 },
    { max: 1000, cols: 2 },
    { max: 1400, cols: 3 },
    { max: Infinity, cols: 4 },
  ],
  comfortable: [
    { max: 700, cols: 1 },
    { max: 1000, cols: 2 },
    { max: 1400, cols: 2 },
    { max: Infinity, cols: 3 },
  ],
}

const colCount = ref(4)

function updateColCount() {
  const width = window.innerWidth
  const list = DENSITY_BREAKPOINTS[props.density || 'standard'] || DENSITY_BREAKPOINTS.standard
  colCount.value = list.find((b) => width <= b.max)?.cols ?? 4
}

watch(() => props.density, updateColCount)

onMounted(() => {
  updateColCount()
  window.addEventListener('resize', updateColCount)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateColCount)
})

/** 行优先分列：第 i 个素材放入第 i % 列数 的列 */
const columns = computed<InspirationOut[][]>(() => {
  const cols: InspirationOut[][] = Array.from({ length: colCount.value }, () => [])
  props.items.forEach((item, i) => cols[i % colCount.value].push(item))
  return cols
})

// ===== 定位模式：列表加载后滚动到首个被定位素材 =====
watch(
  () => props.items,
  async () => {
    if (!props.focusedIds?.length) return
    await nextTick()
    const first = props.items.find((i) => props.focusedIds?.includes(i.id))
    if (!first) return
    document
      .querySelector(`.masonry-cell[data-insp-id="${first.id}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  },
)
</script>

<template>
  <div class="masonry-container">
    <div class="masonry-grid" :class="'density-' + (density || 'standard')">
      <div v-for="(col, ci) in columns" :key="ci" class="masonry-column">
        <div
          v-for="item in col"
          :key="item.id"
          class="masonry-cell"
          :class="{ focused: focusedIds?.includes(item.id) }"
          :data-insp-id="item.id"
        >
          <InspirationCard
            :item="item"
            :badge="badges?.[item.id]"
            :selectable="selectable"
            :selected="selectable ? selectedIds?.has(item.id) : false"
            :show-actions="showActions !== false"
            :hover-zoom="hoverZoom"
            :show-view-button="showViewButton"
            @delete="emit('delete', item.id)"
            @toggle-favorite="emit('toggleFavorite', item.id)"
            @rate="(v: number) => emit('rate', item.id, v)"
            @approve="emit('approve', item.id)"
            @toggle-select="emit('toggleSelect', item.id)"
          />
        </div>
      </div>
    </div>

    <!-- 加载指示器 -->
    <div v-if="loading" class="loading-bar">
      <n-spin size="small" />
      <span style="margin-left: 8px">加载中...</span>
    </div>

    <!-- 空状态 -->
    <n-empty
      v-if="!loading && items.length === 0"
      :description="emptyText || '还没有素材，去上传或采集一些吧'"
      style="margin-top: 80px"
    />
  </div>
</template>

<style scoped>
/* 行优先分列容器：每列等宽、顶部对齐 */
.masonry-grid {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.masonry-grid.density-compact {
  gap: 8px;
}

.masonry-grid.density-comfortable {
  gap: 24px;
}

.masonry-column {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.masonry-grid.density-compact .masonry-column {
  gap: 8px;
}

.masonry-grid.density-comfortable .masonry-column {
  gap: 24px;
}

.masonry-cell {
  width: 100%;
}

/* 定位模式：被定位素材金色描边 + 角标，加载时闪烁提示 */
.masonry-cell.focused {
  position: relative;
  border-radius: 8px;
  box-shadow:
    0 0 0 3px #f0a020,
    0 0 12px rgba(240, 160, 32, 0.45);
  animation: focus-pulse 1.2s ease-in-out 2;
}

.masonry-cell.focused::after {
  content: '已定位';
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 5;
  padding: 2px 8px;
  border-radius: 10px;
  background: #f0a020;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.6;
  pointer-events: none;
}

@keyframes focus-pulse {
  0%,
  100% {
    box-shadow:
      0 0 0 3px #f0a020,
      0 0 12px rgba(240, 160, 32, 0.45);
  }
  50% {
    box-shadow:
      0 0 0 6px #f0a020,
      0 0 20px rgba(240, 160, 32, 0.8);
  }
}

.loading-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: #999;
}
</style>
