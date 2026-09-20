<script setup lang="ts">
/** 素材详情页「移入垃圾桶」原因选择弹窗。
 *
 * 从 DetailView 模板原样抽出（提取式重构，标记与判定逻辑不变）。
 */

import { TRASH_REASON_OPTIONS, type TrashReason } from '@/api/inspirations'

defineProps<{
  /** 提交中（确认按钮 loading + 禁用） */
  submitting: boolean
}>()

const emit = defineEmits<{
  (e: 'confirm'): void
}>()

/** 弹窗开关（原视图状态，经 v-model 双向绑定） */
const visible = defineModel<boolean>('visible', { required: true })
/** 选中的删除原因（未选为 null） */
const reason = defineModel<TrashReason | null>('reason', { required: true })
</script>

<template>
  <a-modal v-model:visible="visible" title="移入垃圾桶" :width="420" :mask-closable="false">
    <p class="trash-reason-tip">
      请选择移入垃圾桶的原因，移入后可在保留期内从「素材管理 → 垃圾桶」恢复：
    </p>
    <a-radio-group
      :model-value="reason ?? undefined"
      class="trash-reason-group"
      @change="(v: unknown) => (reason = (v as TrashReason | undefined) ?? null)"
    >
      <a-space direction="vertical" :size="10">
        <a-radio v-for="opt in TRASH_REASON_OPTIONS" :key="opt.value" :value="opt.value">{{
          opt.label
        }}</a-radio>
      </a-space>
    </a-radio-group>
    <template #footer>
      <div class="trash-modal-footer">
        <a-button @click="visible = false">取消</a-button>
        <a-button
          status="danger"
          :loading="submitting"
          :disabled="!reason"
          @click="emit('confirm')"
        >
          确认移入
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
/* 移入垃圾桶原因弹窗 */
.trash-reason-tip {
  margin: 0 0 14px;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
}

.trash-reason-group {
  display: block;
}

.trash-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
