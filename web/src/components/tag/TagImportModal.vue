<script setup lang="ts">
/** 导入标签弹窗：粘贴 JSON 数组批量导入。 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { importTags } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ imported: [] }>()

const importJsonText = ref('')

async function handleImport() {
  if (!importJsonText.value.trim()) return
  try {
    const tags = JSON.parse(importJsonText.value)
    if (!Array.isArray(tags)) throw new Error('格式错误')
    const data = await importTags(tags)
    Message.success(data.message)
    show.value = false
    importJsonText.value = ''
    emit('imported')
  } catch {
    Message.error('导入失败：请检查 JSON 格式，确保每项包含 name 字段')
  }
}
</script>

<template>
  <a-modal v-model:visible="show" title="导入标签" :footer="false" :width="550">
    <p style="font-size:13px;color:#999;margin-bottom:8px">
      粘贴 JSON 数组，每项含 name 和 category 字段：
    </p>
    <a-textarea
      v-model="importJsonText"
      :auto-size="{ minRows: 10 }"
      placeholder='[{"name": "森系", "category": "style"}, ...]'
    />
    <a-space style="display:flex;justify-content:flex-end;margin-top:16px">
      <a-button @click="show = false">取消</a-button>
      <a-button type="primary" @click="handleImport" :disabled="!importJsonText.trim()">导入</a-button>
    </a-space>
  </a-modal>
</template>
