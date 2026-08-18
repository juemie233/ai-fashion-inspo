<script setup lang="ts">
/** 单个标签合并弹窗：选择目标标签并合并。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import { mergeTags, CATEGORY_LABELS, type TagCategoryGroup } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ merged: [] }>()

const props = defineProps<{
  source: { id: number; name: string } | null
  groups: TagCategoryGroup[]
}>()

const message = useMessage()

const mergeTarget = ref<number | null>(null)

const mergeTargetOptions = computed(() => {
  if (!props.source) return [] as Array<{ label: string; value: number }>
  const opts: Array<{ label: string; value: number }> = []
  for (const group of props.groups) {
    for (const tag of group.tags) {
      if (tag.id !== props.source.id) {
        opts.push({ label: `${tag.name} (${CATEGORY_LABELS[tag.category] || tag.category})`, value: tag.id })
      }
    }
  }
  return opts
})

async function handleMerge() {
  if (!props.source || !mergeTarget.value) return
  try {
    await mergeTags(props.source.id, mergeTarget.value)
    message.success('标签已合并')
    show.value = false
    mergeTarget.value = null
    emit('merged')
  } catch (e) { message.error(getApiErrorMessage(e, '合并失败')) }
}
</script>

<template>
  <n-modal v-model:show="show" title="合并标签" preset="card" style="width:500px">
    <p v-if="source">将 <strong>{{ source.name }}</strong> 合并到：</p>
    <n-select
      v-model:value="mergeTarget"
      :options="mergeTargetOptions"
      placeholder="选择目标标签"
      filterable
      style="margin:16px 0"
    />
    <n-space justify="end">
      <n-button @click="show = false">取消</n-button>
      <n-button type="primary" @click="handleMerge" :disabled="!mergeTarget">确认合并</n-button>
    </n-space>
  </n-modal>
</template>
