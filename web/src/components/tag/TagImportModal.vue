<script setup lang="ts">
/** 导入标签弹窗：粘贴 JSON 数组批量导入。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { importTags } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ imported: [] }>()

const message = useMessage()

const importJsonText = ref('')

async function handleImport() {
  if (!importJsonText.value.trim()) return
  try {
    const tags = JSON.parse(importJsonText.value)
    if (!Array.isArray(tags)) throw new Error('格式错误')
    const data = await importTags(tags)
    message.success(data.message)
    show.value = false
    importJsonText.value = ''
    emit('imported')
  } catch (e: any) {
    message.error('导入失败：请检查 JSON 格式，确保每项包含 name 字段')
  }
}
</script>

<template>
  <n-modal v-model:show="show" title="导入标签" preset="card" style="width:550px">
    <p style="font-size:13px;color:#999;margin-bottom:8px">
      粘贴 JSON 数组，每项含 name 和 category 字段：
    </p>
    <n-input
      v-model:value="importJsonText"
      type="textarea"
      :rows="10"
      placeholder='[{"name": "森系", "category": "style"}, ...]'
    />
    <n-space justify="end" style="margin-top:16px">
      <n-button @click="show = false">取消</n-button>
      <n-button type="primary" @click="handleImport" :disabled="!importJsonText.trim()">导入</n-button>
    </n-space>
  </n-modal>
</template>
