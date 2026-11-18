<script setup lang="ts">
/** 随机评分页：每次随机抽一张图片素材，1-5 星评分后自动换下一张。 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getFileUrl, type TrashReason } from '@/api/inspirations'
import AsyncState from '@/components/common/AsyncState.vue'
import RatingControls from '@/components/rating/RatingControls.vue'
import RatingImageStage from '@/components/rating/RatingImageStage.vue'
import TrashReasonModal from '@/components/inspiration/TrashReasonModal.vue'
import { useRandomRating } from '@/composables/useRandomRating'
import { formatDate } from '@/utils/format'
import { sourceLabel } from '@/utils/sourceLabel'

const router = useRouter()
const {
  item,
  total,
  showingUnrated,
  loading,
  saving,
  deleting,
  ratedCount,
  error,
  loadRandom,
  rate,
  removeCurrent,
} = useRandomRating()

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

/** 舞台下方展示的标签名（最多 8 个，公共组件只接收字符串） */
const tagNames = computed(() => item.value?.tags?.slice(0, 8).map((t) => t.tag.name) ?? [])

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
  if (saving.value || deleting.value || trashModalOpen.value) return
  clearNextTimer()
  void loadRandom()
}

/** 删除当前图片：先弹公共「选择删除原因」弹窗，确认后软删除并抽下一张 */
const trashModalOpen = ref(false)
const deleteTargetId = ref<string | null>(null)

function openDeleteConfirm(): void {
  deleteTargetId.value = item.value?.id ?? null
  if (!deleteTargetId.value) return
  clearNextTimer()
  trashModalOpen.value = true
}

async function confirmTrash(reason: TrashReason): Promise<void> {
  trashModalOpen.value = false
  const targetId = deleteTargetId.value
  deleteTargetId.value = null
  if (!targetId || saving.value || deleting.value) return
  if (await removeCurrent(targetId, reason)) await loadRandom()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.repeat) return
  if (trashModalOpen.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      trashModalOpen.value = false
      deleteTargetId.value = null
    }
    return
  }
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
        <h2 class="page-title">随机评分</h2>
        <span class="rate-subtitle">仅图片素材 · 优先未评分 · 本次已评 {{ ratedCount }} 张</span>
      </div>
      <span v-if="total > 0" class="rate-total">
        {{ showingUnrated ? `待评分 ${total} 张` : `已评分图片 ${total} 张` }}
      </span>
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
        <!-- 图片舞台与评分控件是公共组件（人物优先评分页共用，避免两页各写一份） -->
        <RatingImageStage
          :src="imageUrl"
          :alt="item.source_author || '待评分素材'"
          :loading="loading"
          :meta-text="metaText"
          :tags="tagNames"
        />
        <RatingControls
          :model-value="item.rating || 0"
          :saving="saving"
          :loading="loading"
          :deleting="deleting"
          keyboard-hint="空格 / → 换一张 · Esc 返回素材库 · 删除需选择原因"
          @rate="handleRate"
          @delete="openDeleteConfirm"
          @next="nextImage"
        />
      </div>
    </AsyncState>

    <!-- 删除原因必选（复用素材库公共组件），确认后软删除到垃圾桶 -->
    <TrashReasonModal v-model:visible="trashModalOpen" :count="1" @confirm="confirmTrash" />
  </div>
</template>

<style scoped>
/* 印样台：随机评分是「审片台」，一张样张 + 一条评分尺。
 * 舞台/信息行/评分控件已抽到 components/rating 公共组件，这里只留页面级布局。 */
.rate-page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 0 32px;
}

.rate-page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  align-items: flex-end;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.rate-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: 1;
}

.rate-title-wrap h2.page-title {
  border: 0;
  padding: 0;
  margin: 0;
  font-size: 30px;
}

.rate-subtitle,
.rate-total {
  color: var(--ink-2);
  font-size: 13px;
}

.rate-total {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
}

.rate-card {
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 16px;
}

.rate-error {
  margin-bottom: 12px;
}

@media (max-width: 767px) {
  .rate-page-header {
    flex-wrap: wrap;
  }
}
</style>
