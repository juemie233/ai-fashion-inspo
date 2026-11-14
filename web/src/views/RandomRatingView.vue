<script setup lang="ts">
/** 随机评分页：每次随机抽一张图片素材，1-5 星评分后自动换下一张。 */

import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getFileUrl } from '@/api/inspirations'
import AsyncState from '@/components/common/AsyncState.vue'
import { useRandomRating } from '@/composables/useRandomRating'
import { formatDate } from '@/utils/format'
import { sourceLabel } from '@/utils/sourceLabel'

const router = useRouter()
const { item, total, loading, saving, ratedCount, error, loadRandom, rate } = useRandomRating()

/** 当前图片大图地址（评分看原图，避免缩略图看不清细节） */
const imageUrl = computed(() => (item.value ? getFileUrl(item.value.file_path) : ''))

/** 图片信息摘要：来源 / 作者 / 入库时间 / 审核状态 */
const metaText = computed(() => {
  const row = item.value
  if (!row) return ''
  const parts: string[] = []
  if (row.source_type) parts.push(sourceLabel(row.source_type))
  if (row.source_author) parts.push(row.source_author)
  if (row.created_at) parts.push(`入库 ${formatDate(row.created_at)}`)
  if (row.quality_status === 'approved') parts.push('已通过审核')
  else if (row.quality_status === 'rejected') parts.push('已被拒绝')
  return parts.join(' · ')
})

/** 评分后自动换下一张的计时器（换图/离开页面时清掉，避免重复请求） */
let nextTimer: ReturnType<typeof setTimeout> | null = null

function clearNextTimer(): void {
  if (nextTimer !== null) {
    clearTimeout(nextTimer)
    nextTimer = null
  }
}

function scheduleNext(): void {
  clearNextTimer()
  nextTimer = setTimeout(() => {
    nextTimer = null
    void loadRandom()
  }, 500)
}

async function handleRate(value: number): Promise<void> {
  clearNextTimer()
  if (await rate(value)) {
    // 清除评分（0）不自动跳，方便用户确认
    if (value > 0) scheduleNext()
  }
}

function nextImage(): void {
  if (saving.value) return
  clearNextTimer()
  void loadRandom()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.repeat) return
  if (event.key === 'Escape') {
    event.preventDefault()
    router.push('/')
    return
  }
  if (event.key === ' ' || event.key === 'ArrowRight') {
    event.preventDefault()
    nextImage()
    return
  }
  if (/^[1-5]$/.test(event.key)) {
    event.preventDefault()
    void handleRate(Number(event.key))
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void loadRandom()
})

onUnmounted(() => {
  clearNextTimer()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="rate-page">
    <div class="rate-page-header">
      <a-button type="text" @click="router.push('/')">← 返回素材库</a-button>
      <div class="rate-title-wrap">
        <h2>随机评分</h2>
        <span class="rate-subtitle">仅图片素材 · 本次已评 {{ ratedCount }} 张</span>
      </div>
      <span v-if="total > 0" class="rate-total">图片素材 {{ total }} 张</span>
    </div>

    <a-alert v-if="error && item" type="warning" class="rate-error" closable @close="error = null">
      {{ error }}
    </a-alert>

    <AsyncState
      :loading="loading && !item"
      :error="item ? null : error"
      :empty="!item && !loading && !error"
      empty-text="素材库还没有可评分的图片素材，先去上传或采集一些图片吧"
      loading-text="正在随机抽取图片..."
      @retry="loadRandom"
    >
      <div v-if="item" class="rate-card">
        <a-spin :loading="loading" class="rate-image-spin">
          <div class="rate-stage">
            <img :src="imageUrl" :alt="item.source_author || '待评分素材'" class="rate-image" />
          </div>
        </a-spin>

        <div class="rate-info">
          <div class="rate-meta">{{ metaText }}</div>
          <div v-if="item.tags?.length" class="rate-tags">
            <a-tag v-for="tag in item.tags.slice(0, 8)" :key="tag.tag.id" size="small">
              {{ tag.tag.name }}
            </a-tag>
          </div>
        </div>

        <div class="rate-controls">
          <div class="rate-stars">
            <a-rate
              :model-value="item.rating || 0"
              allow-clear
              :disabled="saving"
              aria-label="给当前图片评分"
              @change="handleRate"
            />
            <span class="rate-hint">点击星星评分，按 1-5 也可</span>
          </div>
          <a-button type="primary" :loading="loading" :disabled="saving" @click="nextImage"
            >换一张 →</a-button
          >
        </div>

        <div class="rate-keyboard-hint">空格 / → 换一张 · Esc 返回素材库</div>
      </div>
    </AsyncState>
  </div>
</template>

<style scoped>
.rate-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 4px 0 32px;
}

.rate-page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.rate-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: 1;
}

.rate-title-wrap h2 {
  margin: 0;
}

.rate-subtitle,
.rate-total {
  color: var(--color-text-3);
  font-size: 13px;
}

.rate-card {
  background: var(--color-bg-2);
  border: 1px solid var(--color-border-2);
  border-radius: 10px;
  padding: 16px;
}

.rate-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  max-height: 68vh;
  overflow: hidden;
  background: var(--color-fill-1);
  border-radius: 8px;
}

.rate-image {
  display: block;
  max-width: 100%;
  max-height: 68vh;
  object-fit: contain;
}

.rate-image-spin {
  display: block;
}

.rate-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.rate-meta {
  color: var(--color-text-2);
  font-size: 13px;
}

.rate-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.rate-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-1);
}

.rate-stars {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 22px;
}

.rate-stars :deep(.arco-rate) {
  font-size: 30px;
}

.rate-hint,
.rate-keyboard-hint {
  color: var(--color-text-3);
  font-size: 12px;
}

.rate-keyboard-hint {
  margin-top: 8px;
  text-align: right;
}

.rate-error {
  margin-bottom: 12px;
}
</style>
