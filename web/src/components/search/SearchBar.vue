<script setup lang="ts">
/** 搜索栏：标签名自动补全 + 关键词搜索 + 智能粘贴。 */

import { ref, watch, onUnmounted } from 'vue'
import { IconSearch } from '@arco-design/web-vue/es/icon'
import { fetchSuggestions } from '@/api/search'

const inputValue = ref('')
const suggestions = ref<Array<{ label: string; value: string }>>([])
let debounceTimer: ReturnType<typeof setTimeout> | null = null

/** 内部自动补全组件实例引用，用于暴露 focus 方法 */
const autoCompleteInst = ref<{ focus: () => void } | null>(null)

// 暴露 focus 方法，供 SearchView 的全局快捷键（按 / 聚焦搜索框）调用
defineExpose({ focus: () => autoCompleteInst.value?.focus() })

const emit = defineEmits<{
  (e: 'search', value: string): void
}>()

/** 服务端标签建议（防抖 200ms） */
watch(inputValue, (val) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!val || val.trim().length < 1) {
    suggestions.value = []
    return
  }
  debounceTimer = setTimeout(async () => {
    try {
      const items = await fetchSuggestions(val.trim())
      if (Array.isArray(items)) {
        suggestions.value = items.slice(0, 8).map((s) => ({
          label: `${s.name} (${s.usage_count})`,
          value: s.name,
        }))
      }
    } catch {
      suggestions.value = []
    }
  }, 200)
})

// 卸载时清理防抖定时器，避免残留请求回调在组件销毁后执行
onUnmounted(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})

/** 智能粘贴：自动识别颜色代码和日期 */
function onPaste(e: ClipboardEvent) {
  const text = e.clipboardData?.getData('text')?.trim()
  if (!text) return
  // 检测颜色代码
  if (/^#[0-9a-fA-F]{6}$/.test(text)) {
    e.preventDefault()
    inputValue.value = text
    emit('search', text)
  }
}

function handleEnter() {
  const val = inputValue.value.trim()
  if (val) emit('search', val)
}

function selectSuggestion(val: string) {
  inputValue.value = val
  emit('search', val)
}
</script>

<template>
  <div class="search-bar">
    <a-auto-complete
      ref="autoCompleteInst"
      v-model="inputValue"
      :data="suggestions"
      placeholder="搜索标签、颜色、关键词... 如「JK制服」「#FF0000」「春季」"
      allow-clear
      @select="selectSuggestion"
      @press-enter="handleEnter"
      @paste="onPaste"
      size="large"
    >
      <template #prefix>
        <IconSearch :size="16" style="margin-right: 6px; color: var(--ink-3)" />
      </template>
    </a-auto-complete>
  </div>
</template>

<style scoped>
.search-bar {
  width: 100%;
}

/* ── 印样台搜索：一条横向检索条，而不是大号圆角搜索框 ── */
.search-bar :deep(.arco-input-wrapper) {
  height: 52px;
  padding: 0 16px;
  background: var(--sheet);
  border: 1px solid var(--line-strong);
  border-radius: 0;
  font-size: 16px;
}

.search-bar :deep(.arco-input-wrapper:hover) {
  border-color: var(--ink-3);
}

.search-bar :deep(.arco-input-wrapper.arco-input-focus) {
  border-color: var(--pine);
  background: var(--sheet);
}

.search-bar :deep(.arco-input) {
  font-size: 16px;
}
</style>
