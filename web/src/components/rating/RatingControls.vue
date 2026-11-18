<script setup lang="ts">
/** 评分页公共「评分控件」：星级 + 删除/换一张 + 键盘提示。
 *
 * 首页随机评分与人物优先评分共用；页面额外动作（如「换一位博主」）通过
 * `extra-actions` 插槽放在删除按钮前面，保证两个页面的操作行结构一致。
 */

withDefaults(
  defineProps<{
    /** 当前评分 0~5（0 = 未评分/清除） */
    modelValue?: number
    /** 保存评分中：禁用星级与操作按钮，防连点 */
    saving?: boolean
    /** 换图/换博主加载中 */
    loading?: boolean
    /** 删除中 */
    deleting?: boolean
    /** 主按钮文案（首页「换一张 →」；人物页「换一张图片 →」） */
    nextText?: string
    /** 星级旁提示 */
    hint?: string
    /** 底部键盘提示（每页 Esc 行为不同，由页面传入） */
    keyboardHint?: string
    /** 是否显示删除按钮（默认显示） */
    showDelete?: boolean
  }>(),
  {
    modelValue: 0,
    saving: false,
    loading: false,
    deleting: false,
    nextText: '换一张 →',
    hint: '点击星星评分，按 1-5 也可',
    keyboardHint: '',
    showDelete: true,
  },
)

const emit = defineEmits<{
  (e: 'rate', value: number): void
  (e: 'delete'): void
  (e: 'next'): void
}>()
</script>

<template>
  <div class="rate-controls">
    <div class="rate-stars">
      <a-rate
        :model-value="modelValue"
        allow-clear
        :disabled="saving"
        aria-label="给当前图片评分"
        @change="(value: number) => emit('rate', value)"
      />
      <span class="rate-hint">{{ hint }}</span>
    </div>
    <div class="rate-actions">
      <slot name="extra-actions" />
      <a-button
        v-if="showDelete"
        status="danger"
        :disabled="saving || loading"
        :loading="deleting"
        @click="emit('delete')"
        >删除</a-button
      >
      <a-button type="primary" :loading="loading" :disabled="saving" @click="emit('next')">
        {{ nextText }}
      </a-button>
    </div>
  </div>
  <div v-if="keyboardHint" class="rate-keyboard-hint">{{ keyboardHint }}</div>
</template>

<style scoped>
.rate-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.rate-stars {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 22px;
}

.rate-stars :deep(.arco-rate) {
  font-size: 30px;
}

.rate-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  row-gap: 8px;
}

.rate-hint,
.rate-keyboard-hint {
  color: var(--ink-3);
  font-size: 12px;
}

.rate-keyboard-hint {
  margin-top: 8px;
  text-align: right;
}

@media (max-width: 767px) {
  .rate-controls {
    flex-wrap: wrap;
    row-gap: 8px;
  }
}
</style>
