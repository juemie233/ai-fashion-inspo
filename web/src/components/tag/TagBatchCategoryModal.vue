<script setup lang="ts">
/** 批量修改类别弹窗：将选中的标签移动到指定类别。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { CATEGORY_LABELS } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

const props = defineProps<{ selectedIds: Set<number> }>()

const message = useMessage()

const batchCategoryTarget = ref('')

async function handleBatchCategory() {
  if (props.selectedIds.size === 0 || !batchCategoryTarget.value) return
  try {
    const { data } = await apiClient.patch('/tags/batch-category', {
      tag_ids: [...props.selectedIds], category: batchCategoryTarget.value,
    })
    message.success(`已将 ${data.updated} 个标签移至指定类别`)
    show.value = false
    batchCategoryTarget.value = ''
    emit('done')
  } catch (e: any) { message.error(e.response?.data?.detail || '操作失败') }
}
</script>

<template>
  <n-modal v-model:show="show" title="批量修改类别" preset="card" style="width:420px">
    <p>将选中的 {{ selectedIds.size }} 个标签移至：</p>
    <n-select
      v-model:value="batchCategoryTarget"
      :options="Object.entries(CATEGORY_LABELS).map(([k,v])=>({label:v,value:k}))"
      style="margin:12px 0"
    />
    <n-space justify="end">
      <n-button @click="show = false">取消</n-button>
      <n-button type="primary" @click="handleBatchCategory" :disabled="!batchCategoryTarget">确认</n-button>
    </n-space>
  </n-modal>
</template>
