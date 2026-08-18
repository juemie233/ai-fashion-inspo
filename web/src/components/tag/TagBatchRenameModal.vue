<script setup lang="ts">
/** 批量重命名弹窗：在选中的标签名中查找并替换。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ done: [] }>()

const props = defineProps<{ selectedIds: Set<number> }>()

const message = useMessage()

const renameFind = ref('')
const renameReplace = ref('')

async function handleBatchRename() {
  if (props.selectedIds.size === 0 || !renameFind.value.trim() || !renameReplace.value.trim()) return
  try {
    const { data } = await apiClient.patch('/tags/batch-rename', {
      tag_ids: [...props.selectedIds], find: renameFind.value.trim(), replace: renameReplace.value.trim(),
    })
    message.success(`已更新 ${data.updated} 个标签`)
    show.value = false
    renameFind.value = ''
    renameReplace.value = ''
    emit('done')
  } catch (e) { message.error(getApiErrorMessage(e, '操作失败')) }
}
</script>

<template>
  <n-modal v-model:show="show" title="批量重命名" preset="card" style="width:420px">
    <p>在选中的 {{ selectedIds.size }} 个标签中查找替换：</p>
    <n-form label-placement="left" label-width="60" size="small">
      <n-form-item label="查找"><n-input v-model:value="renameFind" placeholder="如: 白色" /></n-form-item>
      <n-form-item label="替换为"><n-input v-model:value="renameReplace" placeholder="如: 纯白" /></n-form-item>
    </n-form>
    <n-space justify="end">
      <n-button @click="show = false">取消</n-button>
      <n-button type="primary" @click="handleBatchRename" :disabled="!renameFind.trim()||!renameReplace.trim()">确认</n-button>
    </n-space>
  </n-modal>
</template>
