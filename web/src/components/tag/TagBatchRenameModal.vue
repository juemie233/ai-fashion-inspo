<script setup lang="ts">
/** 批量重命名弹窗：在选中的标签名中查找并替换。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

const props = defineProps<{ selectedIds: Set<number> }>()

const renameFind = ref('')
const renameReplace = ref('')

async function handleBatchRename() {
  if (props.selectedIds.size === 0 || !renameFind.value.trim() || !renameReplace.value.trim()) return
  try {
    const { data } = await apiClient.patch('/tags/batch-rename', {
      tag_ids: [...props.selectedIds], find: renameFind.value.trim(), replace: renameReplace.value.trim(),
    })
    Message.success(`已更新 ${data.updated} 个标签`)
    show.value = false
    renameFind.value = ''
    renameReplace.value = ''
    emit('done')
  } catch (e) { Message.error(getApiErrorMessage(e, '操作失败')) }
}
</script>

<template>
  <a-modal v-model:visible="show" title="批量重命名" :footer="false" :width="420">
    <p>在选中的 {{ selectedIds.size }} 个标签中查找替换：</p>
    <a-form :model="{ renameFind, renameReplace }" label-align="left" :label-col-style="{ width: '60px' }" size="small">
      <a-form-item label="查找"><a-input v-model="renameFind" placeholder="如: 白色" /></a-form-item>
      <a-form-item label="替换为"><a-input v-model="renameReplace" placeholder="如: 纯白" /></a-form-item>
    </a-form>
    <a-space style="display:flex;justify-content:flex-end">
      <a-button @click="show = false">取消</a-button>
      <a-button type="primary" @click="handleBatchRename" :disabled="!renameFind.trim()||!renameReplace.trim()">确认</a-button>
    </a-space>
  </a-modal>
</template>
