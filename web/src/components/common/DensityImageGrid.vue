<script setup lang="ts">
/**
 * 通用密度图片网格：按「紧凑 / 标准 / 宽松」三档密度渲染网格容器。
 *
 * 本组件不关心卡片内容，由父组件通过默认插槽注入每个网格单元（纯 CSS 网格布局）；
 * 密度切换按钮与网格列数 / 间距由本组件统一管理，供素材库 / 剪裁等页面复用，
 * 避免各页面重复实现密度控制与网格样式。密度为 v-model：父组件持有状态并自行持久化。
 *
 * 用法：
 *   <DensityImageGrid v-model:density="density">
 *     <template #header-left> …工具栏内容（可选）… </template>
 *     <div v-for="item in items" :key="item.id" class="my-card">…</div>
 *   </DensityImageGrid>
 */

export type DensityMode = 'compact' | 'standard' | 'comfortable'

/** 密度选项展示文案（与素材库页面一致） */
const DENSITY_OPTIONS: { label: string; value: DensityMode }[] = [
  { label: '紧凑', value: 'compact' },
  { label: '标准', value: 'standard' },
  { label: '宽松', value: 'comfortable' },
]

withDefaults(
  defineProps<{
    /** 当前密度（v-model），默认为标准 */
    density?: DensityMode
    /** 是否展示密度切换按钮组，默认展示 */
    showSwitch?: boolean
  }>(),
  {
    density: 'standard',
    showSwitch: true,
  },
)

const emit = defineEmits<{
  (e: 'update:density', v: DensityMode): void
}>()
</script>

<template>
  <div class="density-grid">
    <!-- 头部：左侧工具栏插槽 + 右侧密度切换；隐藏切换按钮时仍保留左侧插槽 -->
    <div v-if="showSwitch || $slots['header-left']" class="density-grid-header">
      <div class="density-grid-header-left"><slot name="header-left" /></div>
      <a-button-group v-if="showSwitch" size="mini">
        <a-button
          v-for="d in DENSITY_OPTIONS"
          :key="d.value"
          :type="density === d.value ? 'primary' : 'secondary'"
          @click="emit('update:density', d.value)"
        >
          {{ d.label }}
        </a-button>
      </a-button-group>
    </div>

    <!-- 网格主体：密度决定列数与间距，默认插槽内容直接作为网格单元 -->
    <div :class="['density-grid-body', 'density-' + density]">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.density-grid-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.density-grid-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-width: 0;
  flex: 1;
}

/* 网格：auto-fill + minmax，随可用宽度自适应列数 */
.density-grid-body {
  display: grid;
}

.density-grid-body.density-compact {
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 6px;
}

.density-grid-body.density-standard {
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
}

.density-grid-body.density-comfortable {
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 16px;
}
</style>
