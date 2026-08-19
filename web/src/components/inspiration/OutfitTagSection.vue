<script setup lang="ts">
/** 穿搭大标签区块：已有标签展示、手动添加、AI 建议与一键入库。 */

import { computed } from 'vue'
import type { InspirationTagOut } from '@/api/inspirations'

const props = defineProps<{
  /** 当前素材的穿搭大标签 */
  tags: InspirationTagOut[]
  /** 大标签下拉选项（已有标签 + 输入新建） */
  options: { label: string; value: string }[]
  /** 已选择待添加的大标签（v-model:selected） */
  selected: string[]
  /** 手动添加提交中 */
  adding: boolean
  /** AI 建议请求中 */
  aiSuggesting: boolean
  /** AI 建议结果 */
  aiSuggestions: string[]
}>()

const emit = defineEmits<{
  (e: 'update:selected', value: string[]): void
  (e: 'add'): void
  (e: 'remove', tagId: number): void
  (e: 'tag-click', name: string): void
  (e: 'ai-suggest'): void
  (e: 'confirm', name: string): void
  (e: 'confirm-all'): void
  (e: 'dismiss', name: string): void
}>()

/** 待添加大标签双向绑定（供 a-select 的 v-model 使用） */
const selectedModel = computed<string[]>({
  get: () => props.selected,
  set: (val) => emit('update:selected', val),
})

/** 输入新大标签后按两次回车快速添加：第二次回车（输入框已空且有待添加标签）触发添加 */
function onOutfitEnter(e: KeyboardEvent) {
  const inputText = (e.target as HTMLInputElement | null)?.value?.trim() ?? ''
  if (inputText === '' && props.selected.length > 0 && !props.adding) {
    e.preventDefault()
    e.stopPropagation()
    emit('add')
  }
}
</script>

<template>
  <div class="outfit-tags-section">
    <div class="outfit-tags-header">
      <h4>穿搭大标签</h4>
      <a-button size="mini" type="outline" :loading="aiSuggesting" @click="emit('ai-suggest')">
        ✨ AI 生成
      </a-button>
    </div>

    <div v-if="tags.length" class="tag-chips" style="margin-bottom: 8px">
      <a-tag
        v-for="t in tags"
        :key="t.tag.id"
        size="small"
        color="red"
        closable
        class="tag-clickable"
        @close="emit('remove', t.tag.id)"
        @click="emit('tag-click', t.tag.name)"
      >
        {{ t.tag.name }}
      </a-tag>
    </div>
    <div v-else style="font-size: 12px; color: #999; margin-bottom: 8px">暂无大标签</div>

    <div class="outfit-tag-add" @keydown.enter.capture="onOutfitEnter">
      <a-select
        v-model="selectedModel"
        multiple
        allow-create
        size="small"
        placeholder="选择或输入大标签，如「白色系穿搭」"
        :options="options"
        style="flex: 1"
      />
      <a-button
        size="small"
        :loading="adding"
        :disabled="selected.length === 0"
        @click="emit('add')"
        >添加</a-button
      >
    </div>
    <div class="outfit-tag-hint">输入新标签后按两次回车即可快速添加</div>

    <div v-if="aiSuggestions.length" class="outfit-tag-suggestions">
      <div class="outfit-tag-suggestions-header">
        <span style="font-size: 12px; color: #999">AI 建议（点击标签入库，点 ✕ 丢弃）：</span>
        <a-button size="mini" type="secondary" status="warning" @click="emit('confirm-all')"
          >一键全部入库 ({{ aiSuggestions.length }})</a-button
        >
      </div>
      <div class="tag-chips">
        <span
          v-for="name in aiSuggestions"
          :key="name"
          style="display: inline-flex; align-items: center; gap: 2px; margin: 0 6px 4px 0"
        >
          <a-tag size="small" color="orange" style="cursor: pointer" @click="emit('confirm', name)">
            {{ name }}
          </a-tag>
          <a-button size="mini" type="text" status="danger" @click="emit('dismiss', name)"
            >✕</a-button
          >
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.outfit-tags-section {
  border: 1px solid #f0d6dc;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 20px;
  background: #fef6f7;
}

.outfit-tags-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.outfit-tags-header h4 {
  margin: 0;
  font-size: 16px;
}

.outfit-tag-add {
  display: flex;
  gap: 6px;
}

.outfit-tag-hint {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
}

/* AI 建议一键全部入库的操作行 */
.outfit-tag-suggestions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 8px 0 4px;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 可点击跳转搜索的标签 */
.tag-clickable {
  cursor: pointer;
}
</style>
