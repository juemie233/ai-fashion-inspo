<script setup lang="ts">
/** 新建标签表单（含相似标签去重建议）。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import { createTag, getSimilarSuggestions, CATEGORY_LABELS } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ created: [] }>()

const message = useMessage()

const newTagName = ref('')
const newTagCategory = ref('free')
const createSuggestions = ref<Array<{ id: number; name: string; category: string }>>([])
let suggestionDebounce: ReturnType<typeof setTimeout> | null = null

onUnmounted(() => {
  if (suggestionDebounce) clearTimeout(suggestionDebounce)
})

function onNewTagNameInput() {
  if (suggestionDebounce) clearTimeout(suggestionDebounce)
  const name = newTagName.value.trim()
  if (!name || name.length < 2) {
    createSuggestions.value = []
    return
  }
  suggestionDebounce = setTimeout(async () => {
    try { createSuggestions.value = await getSimilarSuggestions(name) }
    catch { createSuggestions.value = [] }
  }, 300)
}

async function handleCreate() {
  if (!newTagName.value.trim()) return
  try {
    await createTag(newTagName.value.trim(), newTagCategory.value)
    message.success('标签已创建')
    show.value = false
    newTagName.value = ''
    createSuggestions.value = []
    emit('created')
  } catch (e) {
    message.error(getApiErrorMessage(e, '创建失败'))
  }
}
</script>

<template>
  <n-card v-if="show" title="创建新标签" size="small" style="margin-bottom:16px">
    <n-space align="flex-end">
      <n-form-item label="标签名">
        <n-input
          v-model:value="newTagName"
          placeholder="例如: 森系"
          @input="onNewTagNameInput"
        />
      </n-form-item>
      <n-form-item label="类别">
        <n-select
          v-model:value="newTagCategory"
          :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
          style="width:140px"
        />
      </n-form-item>
      <n-button type="primary" @click="handleCreate">创建</n-button>
    </n-space>
    <!-- 去重建议 -->
    <div v-if="createSuggestions.length > 0" style="margin-top:8px">
      <span style="font-size:12px;color:#f0a020">⚠ 已有相似标签：</span>
      <n-space :size="4" style="margin-top:4px">
        <n-tag
          v-for="s in createSuggestions"
          :key="s.id"
          size="small"
          type="warning"
        >
          {{ s.name }} ({{ CATEGORY_LABELS[s.category] || s.category }})
        </n-tag>
      </n-space>
    </div>
  </n-card>
</template>
