<script setup lang="ts">
/** 人物优先评分页：按「最缺评分」的博主排队，逐张给未评分图片评分。
 *
 * 与首页「随机评分」的区别：那边全库随机抽一张，这里按人物优先队列推进——
 * 当前博主的未评分图片评完后自动切下一位；顺序由「已评占比」决定（最缺评分在前）。
 * 图片舞台/评分控件复用 components/rating 公共组件，删除原因复用 TrashReasonModal。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getFileUrl, type TrashReason } from '@/api/inspirations'
import AsyncState from '@/components/common/AsyncState.vue'
import RatingControls from '@/components/rating/RatingControls.vue'
import RatingImageStage from '@/components/rating/RatingImageStage.vue'
import TrashReasonModal from '@/components/inspiration/TrashReasonModal.vue'
import { usePriorityRating } from '@/composables/usePriorityRating'
import { formatDate } from '@/utils/format'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'

const router = useRouter()
const {
  queue,
  queueTotal,
  currentBlogger,
  current,
  loading,
  saving,
  deleting,
  ratedCount,
  error,
  finished,
  queuePosition,
  hasNextBlogger,
  averageText,
  loadQueue,
  nextImage,
  nextBlogger,
  rate,
  removeCurrent,
  stop,
} = usePriorityRating()

/** 当前图片大图地址（评分看原图，避免缩略图看不清细节） */
const imageUrl = computed(() => (current.value ? getFileUrl(current.value.file_path) : ''))

/** 图片信息摘要：入库时间 / 审核状态 / 收藏（人物素材接口没有来源字段） */
const metaText = computed(() => {
  const row = current.value
  if (!row) return ''
  const parts: string[] = []
  if (row.created_at) parts.push(`入库 ${formatDate(row.created_at)}`)
  if (row.quality_status === 'approved') parts.push('已通过审核')
  else if (row.quality_status === 'rejected') parts.push('已被拒绝')
  if (row.is_favorite) parts.push('已收藏')
  return parts.join(' · ')
})

/** 当前博主平台中文名 */
const platformLabel = computed(() => {
  const platform = currentBlogger.value?.platform
  return platform ? (PERSON_PLATFORM_LABELS[platform] ?? platform) : ''
})

/** 覆盖率展示（后端给 0~1；防御未来改成 0~100 的口径） */
const coveragePercent = computed(() => {
  const coverage = currentBlogger.value?.coverage ?? 0
  const percent = coverage > 1 ? coverage : coverage * 100
  return `${Math.round(Math.min(100, Math.max(0, percent)))}%`
})

/** 空态文案：队列一开始就空 vs 本轮全部评完 */
const emptyText = computed(() =>
  queue.value.length === 0
    ? '还没有需要评分的博主：等出现带未评分图片的穿搭博主后再来'
    : '本轮优先队列的未评分图片都评完了，稍后随新素材/新博主更新',
)

/** 队列规模提示（后端 total 可能大于本次拉取的条数） */
const queueHint = computed(() => {
  if (queueTotal.value <= 0) return ''
  if (queueTotal.value > queue.value.length) {
    return `共 ${queueTotal.value} 位待评博主（本次载入前 ${queue.value.length} 位）`
  }
  return `共 ${queueTotal.value} 位待评博主`
})

// ── 删除：先选原因，确认后软删除并自动抽下一张/切下一位 ──
const trashModalOpen = ref(false)
const deleteTargetId = ref<string | null>(null)

function openDeleteConfirm(): void {
  const targetId = current.value?.inspiration_id ?? null
  if (!targetId) return
  deleteTargetId.value = targetId
  trashModalOpen.value = true
}

async function confirmTrash(reason: TrashReason): Promise<void> {
  trashModalOpen.value = false
  const targetId = deleteTargetId.value
  deleteTargetId.value = null
  if (!targetId || deleting.value) return
  await removeCurrent(targetId, reason)
}

// ── 键盘：1-5 评分、空格/→ 换一张、Esc 返回人物管理（与随机评分页一致）──
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
  // 焦点在输入框/可编辑元素时不抢键（本页暂无可编辑元素，防御未来加搜索框）
  const target = event.target as HTMLElement | null
  const tag = target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return

  if (event.key === 'Escape') {
    event.preventDefault()
    router.push('/persons')
    return
  }
  if (event.key === ' ' || event.key === 'ArrowRight') {
    event.preventDefault()
    void nextImage()
    return
  }
  if (/^[1-5]$/.test(event.key)) {
    event.preventDefault()
    void rate(Number(event.key))
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void loadQueue()
})

onUnmounted(() => {
  stop()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="rate-page">
    <div class="rate-page-header">
      <a-button type="text" @click="router.push('/persons')">← 返回人物管理</a-button>
      <div class="rate-title-wrap">
        <h2 class="page-title">人物优先评分</h2>
        <span class="rate-subtitle">
          按「最缺评分」的博主排队 · 当前博主评完自动切下一位 · 本次已评
          {{ ratedCount }} 张
        </span>
      </div>
      <span v-if="currentBlogger" class="rate-total">
        队列 {{ queuePosition }}<template v-if="queueHint"> · {{ queueHint }}</template>
      </span>
    </div>

    <a-alert
      v-if="error && currentBlogger"
      type="warning"
      class="rate-error"
      closable
      @close="error = null"
    >
      {{ error }}
    </a-alert>

    <AsyncState
      :loading="loading && !currentBlogger"
      :error="currentBlogger ? null : error"
      :empty="!currentBlogger && !loading && !error"
      :empty-text="emptyText"
      loading-text="正在加载人物优先队列..."
      @retry="loadQueue"
    >
      <section v-if="currentBlogger" class="pr-blogger">
        <div class="pr-blogger-main">
          <a-avatar
            :size="48"
            :image-url="
              currentBlogger.avatar_path ? getFileUrl(currentBlogger.avatar_path) : undefined
            "
          >
            {{ currentBlogger.name.slice(0, 1) }}
          </a-avatar>
          <div class="pr-blogger-name">
            <div class="pr-name-row">
              <strong class="pr-name">{{ currentBlogger.name }}</strong>
              <a-tag v-if="platformLabel" size="small">{{ platformLabel }}</a-tag>
              <span v-if="finished" class="pr-finished-tag">已评完</span>
            </div>
            <div class="pr-blogger-meta">
              <span
                >图片 <b class="u-num">{{ currentBlogger.image_count }}</b></span
              >
              <span
                >已评 <b class="u-num">{{ currentBlogger.rated_count }}</b></span
              >
              <span
                >未评 <b class="u-num">{{ currentBlogger.unrated_count }}</b></span
              >
              <span
                >覆盖率 <b class="u-num">{{ coveragePercent }}</b></span
              >
            </div>
          </div>
        </div>
        <dl class="pr-stats">
          <div class="pr-stat">
            <dt>平均评分</dt>
            <dd class="u-num">{{ averageText }}<em v-if="averageText !== '--'"> / 5</em></dd>
          </div>
          <div class="pr-stat">
            <dt>图片素材</dt>
            <dd class="u-num">{{ currentBlogger.image_count }}</dd>
          </div>
          <div class="pr-stat">
            <dt>未评分</dt>
            <dd class="u-num">{{ currentBlogger.unrated_count }}</dd>
          </div>
        </dl>
      </section>

      <div v-if="current" class="rate-card">
        <!-- 图片舞台与评分控件是公共组件（与首页随机评分共用） -->
        <RatingImageStage
          :src="imageUrl"
          :alt="'待评分图片'"
          :loading="loading"
          :meta-text="metaText"
        />
        <RatingControls
          :model-value="current.rating || 0"
          :saving="saving"
          :loading="loading"
          :deleting="deleting"
          next-text="换一张图片 →"
          keyboard-hint="数字 1-5 评分 · 空格 / → 换一张 · Esc 返回人物管理 · 删除需选择原因"
          @rate="(value: number) => void rate(value)"
          @delete="openDeleteConfirm"
          @next="() => void nextImage()"
        >
          <template #extra-actions>
            <a-button
              :disabled="!hasNextBlogger || loading || saving"
              @click="() => void nextBlogger()"
            >
              换一位博主
            </a-button>
          </template>
        </RatingControls>
      </div>

      <!-- 当前博主还在，但这张评完后的下一张还没抽回来：给个过渡提示而不是空白 -->
      <div v-else-if="currentBlogger && !loading" class="pr-waiting">
        正在抽取 {{ currentBlogger.name }} 的下一张未评分图片...
      </div>
    </AsyncState>

    <!-- 删除原因必选（复用素材库公共组件），确认后软删除到垃圾桶 -->
    <TrashReasonModal v-model:visible="trashModalOpen" :count="1" @confirm="confirmTrash" />
  </div>
</template>

<style scoped>
/* 印样台：审片台 + 人物档案条；公共舞台/评分尺样式在 components/rating 里 */
.rate-page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 0 32px;
}

.rate-page-header {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.rate-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: 1;
  flex-wrap: wrap;
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

.rate-error {
  margin-bottom: 12px;
}

/* 人物档案条：头像 + 名字/平台 + 实时统计 */
.pr-blogger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 16px;
  margin-bottom: 16px;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  flex-wrap: wrap;
}

.pr-blogger-main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.pr-blogger-name {
  min-width: 0;
}

.pr-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pr-name {
  font-size: 17px;
  color: var(--ink);
}

.pr-finished-tag {
  padding: 0 6px;
  font-size: 11px;
  color: var(--seal);
  border: 1px solid var(--seal-soft);
  border-radius: var(--radius-sm);
  background: var(--seal-soft);
}

.pr-blogger-meta {
  display: flex;
  gap: 14px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-2);
  flex-wrap: wrap;
}

.pr-blogger-meta b {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  color: var(--ink);
  font-weight: 500;
}

.pr-stats {
  display: flex;
  gap: 28px;
  margin: 0;
  flex-wrap: wrap;
}

.pr-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pr-stat dt {
  font-size: 11px;
  color: var(--ink-3);
}

.pr-stat dd {
  margin: 0;
  font-size: 20px;
  color: var(--ink);
  line-height: 1.2;
}

.pr-stat dd em {
  font-style: normal;
  font-size: 12px;
  color: var(--ink-3);
}

.rate-card {
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 16px;
}

.pr-waiting {
  padding: 48px 0;
  text-align: center;
  color: var(--ink-3);
  font-size: 13px;
}

@media (max-width: 767px) {
  .rate-page-header {
    flex-wrap: wrap;
  }

  .rate-total {
    width: 100%;
  }

  .pr-blogger {
    align-items: flex-start;
  }

  .pr-stats {
    gap: 18px;
  }
}
</style>
