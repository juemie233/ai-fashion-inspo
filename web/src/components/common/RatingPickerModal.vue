<script setup lang="ts">
/** 公共评分选择弹窗：手动选 1~5 星（可选清除 / 空值确认）。
 *
 * 复用场景：
 * - 素材库批量评分（BatchActionBar）：允许「应用即清除」——0 星提交清除评分；
 * - 人物页「补全评分」：必须明确选 1~5 星，未选择时确定按钮禁用。
 *
 * 只负责选择与校验，不发请求；调用方在 confirm 里自行提交。
 */

import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 是否显示（配合 v-model:visible） */
    visible: boolean
    /** 弹窗标题 */
    title?: string
    /** 说明文案：讲清「这次评什么、不覆盖什么」 */
    description?: string
    /** 确定按钮文案 */
    confirmText?: string
    /** 取消按钮文案 */
    cancelText?: string
    /** 是否提供「清除评分」按钮（提交 rating=0） */
    allowClear?: boolean
    /** 未选星时是否允许确定（批量评分允许；补全评分不允许） */
    allowEmptyConfirm?: boolean
    /** 确定按钮 loading（提交中） */
    loading?: boolean
  }>(),
  {
    title: '选择评分',
    description: '',
    confirmText: '确定',
    cancelText: '取消',
    allowClear: false,
    allowEmptyConfirm: false,
    loading: false,
  },
)

const emit = defineEmits<{
  (e: 'update:visible', visible: boolean): void
  /** rating=0 表示清除评分（仅 allowClear=true 时可能出现） */
  (e: 'confirm', rating: number): void
}>()

/** 0 = 未选择/清除；1~5 = 星数（Arco a-rate allow-clear 回 0） */
const rating = ref(0)

const hasRating = computed(() => rating.value >= 1 && rating.value <= 5)
const canConfirm = computed(() => props.allowEmptyConfirm || hasRating.value)
const hintText = computed(() => {
  if (hasRating.value) return `已选 ${rating.value} 星`
  return props.allowEmptyConfirm ? '未选择（确定即清除评分）' : '请选择 1~5 星'
})

// 每次打开重置为未选择：避免上一次的星级被误带去下一次任务
watch(
  () => props.visible,
  (visible) => {
    if (visible) rating.value = 0
  },
)

function close() {
  emit('update:visible', false)
}

function confirm() {
  if (!canConfirm.value || props.loading) return
  emit('confirm', rating.value)
}

/** 清除评分：批量评分场景等价于「应用 0 星」 */
function clear() {
  if (props.loading) return
  emit('confirm', 0)
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="title"
    :width="420"
    :closable="!loading"
    :mask-closable="!loading"
    @cancel="close"
  >
    <p v-if="description" class="rating-desc">{{ description }}</p>
    <div class="rating-row">
      <a-rate v-model="rating" allow-clear :count="5" :disabled="loading" />
      <span class="rating-hint">{{ hintText }}</span>
    </div>
    <template #footer>
      <div class="rating-footer">
        <a-button size="small" :disabled="loading" @click="close">{{ cancelText }}</a-button>
        <a-button v-if="allowClear" size="small" :disabled="loading" @click="clear">
          清除评分
        </a-button>
        <a-button
          size="small"
          type="primary"
          :loading="loading"
          :disabled="!canConfirm || loading"
          @click="confirm"
        >
          {{ confirmText }}
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
/* 印样台：说明文字是档案注记，星级行保持一行可点 */
.rating-desc {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.8;
  color: var(--ink-2);
}

.rating-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rating-hint {
  font-size: 12px;
  color: var(--ink-3);
}

.rating-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
