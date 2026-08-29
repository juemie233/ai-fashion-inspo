<script setup lang="ts">
/** 删除原因选择弹窗：素材移入垃圾桶前必选原因（质量差/重复/不喜欢/隐私/其他/AI生成）。
 *  原因随素材进入垃圾桶（trash_reason），供垃圾桶筛选与负样本学习归因。 */
import { ref, watch } from 'vue'
import type { TrashReason } from '@/api/inspirations'

const TRASH_REASONS: { value: TrashReason; label: string; desc: string }[] = [
  { value: '质量差', label: '质量差', desc: '模糊、截断、构图不佳' },
  { value: '重复', label: '重复', desc: '与其他素材内容重复' },
  { value: '不喜欢', label: '不喜欢', desc: '内容无参考价值' },
  { value: '隐私', label: '隐私', desc: '包含隐私信息' },
  { value: 'AI生成', label: 'AI 生成', desc: '疑似 AI 生成内容' },
  { value: '其他', label: '其他', desc: '其他原因' },
]

const props = defineProps<{
  visible: boolean
  /** 待移入素材数量（弹窗文案展示用） */
  count: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'confirm', reason: TrashReason): void
}>()

const reason = ref<TrashReason | null>(null)

watch(
  () => props.visible,
  (v) => {
    if (v) reason.value = null // 每次打开重置选择
  },
)

function onOk() {
  if (reason.value) emit('confirm', reason.value)
}
</script>

<template>
  <a-modal
    :visible="visible"
    @update:visible="emit('update:visible', $event)"
    title="选择删除原因"
    :width="420"
    :ok-text="'移入垃圾桶'"
    :ok-button-props="{ disabled: !reason }"
    :mask-closable="false"
    @ok="onOk"
  >
    <p style="margin: 0 0 12px; font-size: 13px; color: #666">
      将 <b>{{ count }}</b> 个素材移入垃圾桶（软删除，保留期内可恢复），请选择原因：
    </p>
    <a-radio-group v-model:value="reason" direction="vertical" style="width: 100%">
      <a-radio v-for="r in TRASH_REASONS" :key="r.value" :value="r.value" style="padding: 4px 0">
        <b>{{ r.label }}</b>
        <span style="margin-left: 8px; font-size: 12px; color: #999">{{ r.desc }}</span>
      </a-radio>
    </a-radio-group>
  </a-modal>
</template>
