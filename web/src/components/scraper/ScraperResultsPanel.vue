<script setup lang="ts">
/** 结果预览面板：展示某任务采集到的图片/视频，支持全选与批量删除。 */

import { getFileUrl } from '@/api/inspirations'

defineProps<{
  items: any[]
  total: number
  loading: boolean
  selectedIds: Set<string>
  deleting: boolean
}>()

const emit = defineEmits<{
  (e: 'select-all'): void
  (e: 'toggle-select', id: string): void
  (e: 'delete-selected'): void
}>()
</script>

<template>
<div class="results-panel">
  <n-spin :show="loading">
    <div class="results-header">
      <span>📋 结果（共 {{ total }} 张）</span>
      <n-space>
        <n-button size="tiny" @click="emit('select-all')">{{ selectedIds.size===items.length?'取消全选':'全选' }}</n-button>
        <n-popconfirm v-if="selectedIds.size>0" @positive-click="emit('delete-selected')">
          <template #trigger><n-button size="tiny" type="error" ghost :loading="deleting">删除 ({{ selectedIds.size }})</n-button></template>
          确定删除 {{ selectedIds.size }} 个素材？
        </n-popconfirm>
      </n-space>
    </div>
    <div v-if="items.length===0&&!loading" class="results-empty">空空如也</div>
    <div v-else class="results-grid">
      <div v-for="item in items" :key="item.id" class="result-card" :class="{selected:selectedIds.has(item.id)}" @click="emit('toggle-select', item.id)">
        <video
          v-if="item.media_type === 'video' && !item.thumbnail_path"
          :src="getFileUrl(item.file_path)"
          muted
          playsinline
          preload="metadata"
        />
        <img v-else :src="getFileUrl(item.thumbnail_path||item.file_path)" loading="lazy" />
        <div class="result-check"><n-checkbox :checked="selectedIds.has(item.id)" size="small" /></div>
      </div>
    </div>
  </n-spin>
</div>
</template>

<style scoped>
.results-panel{margin-top:16px;border:1px solid #e5e7eb;border-radius:8px;padding:16px;background:#fff}
.results-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:14px;font-weight:600}
.results-empty{text-align:center;color:#999;padding:32px 0;font-size:13px}
.results-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;max-height:72vh;overflow-y:auto;padding:4px}
.result-card{position:relative;aspect-ratio:3/4;overflow:hidden;border-radius:6px;border:2px solid transparent;cursor:pointer;transition:border-color .15s;background:#f5f5f5}
.result-card.selected{border-color:#2080f0}
.result-card img,
.result-card video{width:100%;height:100%;object-fit:cover}
.result-check{position:absolute;top:4px;right:4px}
</style>
