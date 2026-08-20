<script setup lang="ts">
/**
 * 统计卡片行：统一的 a-row + a-col + a-card + a-statistic 布局。
 *
 * 数字值走 a-statistic（支持 suffix/precision/占位符/动态颜色）；
 * 字符串值（如存储大小）渲染自绘大文本块（Arco Statistic 的 value
 * 仅接受数字，参照 AdminStatCards 先例）。列数由 span 控制（24 栅格），
 * 缺省 flex:1 均分。
 */

export interface StatItem {
  /** 卡片标题 */
  title: string
  /** 数字值（渲染 a-statistic；与 text 二选一） */
  value?: number
  /** 字符串值（渲染自绘大文本，如 "1.2 GB"；与 value 二选一） */
  text?: string
  /** 数值后缀（如 %），渲染在 statistic 的 suffix 插槽 */
  suffix?: string
  /** 数值样式（如动态颜色） */
  valueStyle?: Record<string, string>
  /** 小数位（statistic precision），默认 0 */
  precision?: number
  /** 值为空时的占位符 */
  placeholder?: string
  /** 卡片底部小字说明（如墓碑表记录的提示文案） */
  note?: string
  /** 主色高亮边框（用于需要突出的卡片） */
  highlight?: boolean
}

withDefaults(
  defineProps<{
    items: StatItem[]
    /** a-col 栅格占位（24 栅格）；缺省时 flex:1 均分 */
    span?: number
    /** 栅格间距（透传 a-row gutter），默认 [12, 12] */
    gutter?: [number, number] | number
    /** 行下边距，默认 16px */
    marginBottom?: string
  }>(),
  {
    span: 0,
    gutter: () => [12, 12],
    marginBottom: '16px',
  },
)
</script>

<template>
  <a-row :gutter="gutter" :style="{ marginBottom }">
    <a-col v-for="item in items" :key="item.title" :flex="span ? undefined : 1" :span="span || undefined">
      <a-card size="small" :style="item.highlight ? 'border-color: rgb(var(--primary-6))' : undefined">
        <a-statistic
          v-if="item.value !== undefined"
          :title="item.title"
          :value="item.value"
          :precision="item.precision"
          :placeholder="item.placeholder"
          :value-style="item.valueStyle"
        >
          <template v-if="item.suffix" #suffix>{{ item.suffix }}</template>
        </a-statistic>
        <div v-else class="stat-custom">
          <span class="stat-custom-title">{{ item.title }}</span>
          <span class="stat-custom-value" :style="item.valueStyle">{{ item.text ?? '-' }}</span>
        </div>
        <div v-if="item.note" class="stat-note">{{ item.note }}</div>
      </a-card>
    </a-col>
  </a-row>
</template>

<style scoped>
/* 自绘统计块（Arco Statistic 的 value 不接受字符串，尺寸类用文本展示） */
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

.stat-note {
  font-size: 11px;
  color: var(--color-text-3);
  margin-top: 6px;
}
</style>
