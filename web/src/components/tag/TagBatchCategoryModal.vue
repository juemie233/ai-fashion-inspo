<script setup lang="ts">
/** 批量修改类别弹窗：将选中的标签移动到指定类别。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { CATEGORY_LABELS } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

const props = defineProps<{ selectedIds: Set<number> }>()

const batchCategoryTarget = ref('')

async function handleBatchCategory() {
  if (props.selectedIds.size === 0 || !batchCategoryTarget.value) return
  try {
    const { data } = await apiClient.patch('/tags/batch-category', {
      tag_ids: [...props.selectedIds], category: batchCategoryTarget.value,
    })
    Message.success(`已将 ${data.updated} 个标签移至指定类别`)
    show.value = false
    batchCategoryTarget.value = ''
    emit('done')
  } catch (e) { Message.error(getApiErrorMessage(e, '操作失败')) }
}
</script>

<template>
  <a-modal v-model:visible="show" title="批量修改类别" :footer="false" :width="420">
    <p>将选中的 {{ selectedIds.size }} 个标签移至：</p>
    <a-select
      v-model="batchCategoryTarget"
      :options="Object.entries(CATEGORY_LABELS).map(([k,v])=>({label:v,value:k}))"
      style="margin:12px 0"
    />
    <a-space style="display:flex;justify-content:flex-end">
      <a-button @click="show = false">取消</a-button>
      <a-button type="primary" @click="handleBatchCategory" :disabled="!batchCategoryTarget">确认</a-button>
    </a-space>
  </a-modal>
</template>
