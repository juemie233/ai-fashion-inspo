<script setup lang="ts">
/** 批量合并弹窗：将选中的多个标签合并到同一个目标标签。 */

import { ref, computed, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { mergeTags, type TagCategoryGroup } from '@/api/tags'
import { CATEGORY_LABELS } from '@/constants/tag'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

const props = defineProps<{
  selectedIds: Set<number>
  groups: TagCategoryGroup[]
}>()

const batchMergeTarget = ref<number | null>(null)

watch(show, (v) => {
  if (v) batchMergeTarget.value = null
})

const batchMergeTargetOptions = computed(() => {
  const opts: Array<{ label: string; value: number }> = []
  for (const group of props.groups) {
    for (const tag of group.tags) {
      opts.push({
        label: `${tag.name} (${CATEGORY_LABELS[tag.category] || tag.category})`,
        value: tag.id,
      })
    }
  }
  return opts
})

async function handleBatchMerge() {
  if (!batchMergeTarget.value || props.selectedIds.size < 2) return
  const sourceIds = Array.from(props.selectedIds).filter((id) => id !== batchMergeTarget.value)
  if (sourceIds.length === 0) {
    Message.warning('目标标签不能在被选中的标签中')
    return
  }
  try {
    for (const sid of sourceIds) {
      await mergeTags(sid, batchMergeTarget.value)
    }
    Message.success(`已将 ${sourceIds.length} 个标签合并`)
    show.value = false
    emit('done')
  } catch {
    Message.error('批量合并失败')
  }
}
</script>

<template>
  <a-modal v-model:visible="show" title="批量合并" :footer="false" :width="500">
    <p>将选中的 {{ selectedIds.size }} 个标签合并到：</p>
    <a-select
      :model-value="batchMergeTarget ?? undefined"
      :options="batchMergeTargetOptions"
      placeholder="选择目标标签"
      allow-search
      style="margin: 16px 0"
      @change="(v: unknown) => (batchMergeTarget = (v as number | undefined) ?? null)"
    />
    <a-space style="display: flex; justify-content: flex-end">
      <a-button @click="show = false">取消</a-button>
      <a-button type="primary" @click="handleBatchMerge" :disabled="!batchMergeTarget"
        >确认合并</a-button
      >
    </a-space>
  </a-modal>
</template>
