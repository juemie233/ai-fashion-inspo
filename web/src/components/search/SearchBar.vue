<script setup lang="ts">
/** 搜索栏：支持标签名自动补全。 */

import { ref, computed } from 'vue'
import { useTagsStore } from '@/stores/tags'

const tagsStore = useTagsStore()

const inputValue = ref('')

const emit = defineEmits<{
  (e: 'search', tags: string[]): void
}>()

/** 根据输入过滤标签建议 */
const suggestions = computed(() => {
  const val = inputValue.value.trim().toLowerCase()
  if (!val) return []
  const allTags: string[] = []
  for (const group of tagsStore.groups) {
    for (const tag of group.tags) {
      if (tag.name.toLowerCase().includes(val) && !allTags.includes(tag.name)) {
        allTags.push(tag.name)
      }
    }
  }
  return allTags.slice(0, 8)
})

function handleEnter() {
  const tags = inputValue.value
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
  if (tags.length > 0) {
    emit('search', tags)
  }
}

function selectSuggestion(tag: string) {
  inputValue.value = tag
  emit('search', [tag])
}
</script>

<template>
  <div class="search-bar">
    <n-auto-complete
      v-model:value="inputValue"
      :options="suggestions.map((s) => ({ label: s, value: s }))"
      placeholder="输入标签搜索，多个用逗号分隔 (如: JK制服, 白色, 过膝袜)"
      :input-props="{
        autocomplete: 'disabled',
      }"
      clearable
      @select="selectSuggestion"
      @keyup.enter="handleEnter"
      size="large"
    />
  </div>
</template>

<style scoped>
.search-bar {
  width: 100%;
}
</style>
