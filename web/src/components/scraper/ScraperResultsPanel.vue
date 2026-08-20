<script setup lang="ts">
/** 结果预览面板：展示某任务采集到的图片/视频，支持勾选批量删除、加载更多与跳转素材详情。 */

import { useRouter } from 'vue-router'
import { getFileUrl } from '@/api/inspirations'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'

/** 结果条目：id/媒体类型/文件路径为通用字段，其余字段原样透传 */
interface ResultItem {
  id: string
  media_type?: string
  file_path?: string | null
  thumbnail_path?: string | null
  [key: string]: unknown
}

defineProps<{
  items: ResultItem[]
  total: number
  loading: boolean
  hasMore: boolean
  selectedIds: Set<string>
  deleting: boolean
}>()

const emit = defineEmits<{
  (e: 'select-all'): void
  (e: 'load-more'): void
  (e: 'toggle-select', id: string): void
  (e: 'delete-selected'): void
}>()

const router = useRouter()

/** 跳转到素材详情页（打标签、审核等操作在详情页完成） */
function openDetail(id: string) {
  router.push({ name: 'detail', params: { id } })
}

/** 悬停预览用大图路径：视频素材回退首帧缩略图，图片用原图保证清晰（由 HoverImagePreview 组件驱动） */
function previewSrc(item: ResultItem): string {
  if (item.media_type === 'video') return getFileUrl(item.thumbnail_path || item.file_path || '')
  return getFileUrl(item.file_path || item.thumbnail_path || '')
}
</script>

<template>
<div class="results-panel">
  <a-spin :loading="loading">
    <div class="results-header">
      <span>📋 结果（已加载 {{ items.length }}/{{ total }} 张）</span>
      <a-space>
        <a-button size="mini" @click="emit('select-all')">{{ selectedIds.size===items.length?'取消全选':'全选' }}</a-button>
        <a-popconfirm
          v-if="selectedIds.size>0"
          :content="`确定将 ${selectedIds.size} 个素材移入垃圾桶？（可在管理页垃圾桶恢复）`"
          @ok="emit('delete-selected')"
        >
          <a-button size="mini" type="outline" status="danger" :loading="deleting">删除 ({{ selectedIds.size }})</a-button>
        </a-popconfirm>
      </a-space>
    </div>
    <div v-if="items.length===0&&!loading" class="results-empty">空空如也</div>
    <div v-else class="results-grid">
      <div
        v-for="item in items"
        :key="item.id"
        class="result-card"
        :class="{ selected: selectedIds.has(item.id) }"
        @click="emit('toggle-select', item.id)"
      >
        <HoverImagePreview :large-src="previewSrc(item)">
          <video
            v-if="item.media_type === 'video' && !item.thumbnail_path"
            :src="getFileUrl(item.file_path)"
            muted
            playsinline
            preload="metadata"
          />
          <img v-else :src="getFileUrl(item.thumbnail_path||item.file_path)" loading="lazy" />
        </HoverImagePreview>
        <div class="result-check"><a-checkbox :model-value="selectedIds.has(item.id)" size="small" /></div>
        <a-button class="result-open" size="mini" type="text" @click.stop="openDetail(item.id)">查看详情</a-button>
      </div>
    </div>
    <div v-if="hasMore" class="results-more">
      <a-button size="small" :loading="loading" @click="emit('load-more')">加载更多</a-button>
    </div>
  </a-spin>
</div>
</template>

<style scoped>
.results-panel{margin-top:16px;border:1px solid #e5e7eb;border-radius:8px;padding:16px;background:#fff}
.results-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:14px;font-weight:600}
.results-empty{text-align:center;color:#999;padding:32px 0;font-size:13px}
/* 结果网格：桌面端固定六列（≥1200px），窄屏按档位降列 */
.results-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;max-height:72vh;overflow-y:auto;padding:4px}
@media (max-width:1200px){.results-grid{grid-template-columns:repeat(5,1fr)}}
@media (max-width:900px){.results-grid{grid-template-columns:repeat(4,1fr)}}
@media (max-width:600px){.results-grid{grid-template-columns:repeat(3,1fr)}}
.result-card{position:relative;aspect-ratio:3/4;overflow:hidden;border-radius:6px;border:2px solid transparent;cursor:pointer;transition:border-color .15s;background:#f5f5f5}
.result-card.selected{border-color:#2080f0}
/* 悬停预览触发区域撑满卡片（HoverImagePreview 根元素），图片 100% 高度生效 */
.result-card :deep(.hover-preview-trigger){height:100%}
.result-card img,
.result-card video{width:100%;height:100%;object-fit:cover}
.result-check{position:absolute;top:4px;right:4px}
.result-open{position:absolute;bottom:4px;left:4px;opacity:0;transition:opacity .15s;background:rgba(255,255,255,.85)}
.result-card:hover .result-open{opacity:1}
.results-more{display:flex;justify-content:center;margin-top:12px}
/* 悬停大图浮层由通用组件 HoverImagePreview 承担 */
</style>
