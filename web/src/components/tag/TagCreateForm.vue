<script setup lang="ts">
/** 新建标签表单（含相似标签去重建议）。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, onUnmounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { createTag, getSimilarSuggestions, CATEGORY_LABELS } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ created: [] }>()

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
    Message.success('标签已创建')
    show.value = false
    newTagName.value = ''
    createSuggestions.value = []
    emit('created')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建失败'))
  }
}
</script>

<template>
  <a-card v-if="show" title="创建新标签" size="small" style="margin-bottom:16px">
    <a-space :align="'end'" wrap>
      <a-form :model="{ newTagName, newTagCategory }" layout="inline">
        <a-form-item label="标签名">
          <a-input
            v-model="newTagName"
            placeholder="例如: 森系"
            @input="onNewTagNameInput"
          />
        </a-form-item>
        <a-form-item label="类别">
          <a-select
            v-model="newTagCategory"
            :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
            style="width:140px"
          />
        </a-form-item>
      </a-form>
      <a-button type="primary" @click="handleCreate">创建</a-button>
    </a-space>
    <!-- 去重建议 -->
    <div v-if="createSuggestions.length > 0" style="margin-top:8px">
      <span style="font-size:12px;color:#f0a020">⚠ 已有相似标签：</span>
      <a-space :size="4" style="margin-top:4px" wrap>
        <a-tag
          v-for="s in createSuggestions"
          :key="s.id"
          size="small"
          color="orange"
        >
          {{ s.name }} ({{ CATEGORY_LABELS[s.category] || s.category }})
        </a-tag>
      </a-space>
    </div>
  </a-card>
</template>
