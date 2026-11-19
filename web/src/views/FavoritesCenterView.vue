<script setup lang="ts">
/** 收藏中心：收藏统计 + 最近收藏 + 收藏动态。
 *
 * 目的：素材库/详情/合集里点过的收藏散落在各处，这里集中给出「收藏行为」的视角：
 * 总收藏、本周新增、未评分收藏、未整理收藏，以及最近收藏和收藏/取消流水。
 * 与「收藏合集」分工：合集看归属，收藏中心看行为与待处理（未评分/未整理）。
 */

import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import StatCardGrid from '@/components/common/StatCardGrid.vue'
import AsyncState from '@/components/common/AsyncState.vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { getFileUrl, toggleFavorite } from '@/api/inspirations'
import {
  favoriteActionLabel,
  favoriteSourceLabel,
  fetchFavoritesHistory,
  fetchFavoritesOverview,
  isUnfavoriteAction,
  type FavoriteHistoryItem,
  type FavoritesOverviewOut,
} from '@/api/favorites'
import { getApiErrorMessage } from '@/utils/apiError'
import { formatDateTime } from '@/utils/format'
import { openInspiration } from '@/utils/openInspiration'

const router = useRouter()

// ── 概览：统计卡 + 最近收藏 ──
const overview = ref<FavoritesOverviewOut | null>(null)
const overviewLoading = ref(false)
const overviewError = ref<string | null>(null)

/** 收藏请求中的素材 id：卡片收藏按钮防连点 */
const pendingFavoriteIds = ref<Set<string>>(new Set())

const recent = computed(() => overview.value?.recent ?? [])

/** 统计卡：全部收藏 / 本周新增 / 未评分 / 未整理 */
const statItems = computed(() => {
  const data = overview.value
  if (!data) return []
  return [
    { title: '总收藏', value: data.total_favorites, highlight: true, note: '全库收藏素材' },
    { title: '本周新增', value: data.favorites_this_week, note: '最近 7 天收藏' },
    { title: '未评分收藏', value: data.unrated_favorites, note: '收藏后还没打星' },
    { title: '未整理收藏', value: data.unfiled_favorites, note: '还没归入任何合集' },
  ]
})

async function loadOverview(): Promise<void> {
  overviewLoading.value = true
  overviewError.value = null
  try {
    overview.value = await fetchFavoritesOverview(12)
  } catch (e) {
    // 失败不伪装成空态：AsyncState 会显示错误与重试
    overviewError.value = getApiErrorMessage(e, '加载收藏概览失败')
    overview.value = null
  } finally {
    overviewLoading.value = false
  }
}

/** 最近收藏里取消收藏：刷新概览（卡片从最近收藏消失）与动态列表 */
async function handleRecentFavorite(id: string): Promise<void> {
  if (pendingFavoriteIds.value.has(id)) return
  pendingFavoriteIds.value = new Set(pendingFavoriteIds.value).add(id)
  try {
    await toggleFavorite(id, false)
    Message.success('已取消收藏')
    await Promise.all([loadOverview(), loadHistory()])
  } catch (e) {
    Message.error(getApiErrorMessage(e, '取消收藏失败'))
  } finally {
    const next = new Set(pendingFavoriteIds.value)
    next.delete(id)
    pendingFavoriteIds.value = next
  }
}

// ── 收藏动态 ──
const history = ref<FavoriteHistoryItem[]>([])
const historyLoading = ref(false)
const historyError = ref<string | null>(null)

async function loadHistory(): Promise<void> {
  historyLoading.value = true
  historyError.value = null
  try {
    const data = await fetchFavoritesHistory(30)
    history.value = data.items ?? []
  } catch (e) {
    historyError.value = getApiErrorMessage(e, '加载收藏动态失败')
    history.value = []
  } finally {
    historyLoading.value = false
  }
}

/** 动态条目缩略图（优先缩略图，兜底原图；素材已删除则空态） */
function historyThumbUrl(entry: FavoriteHistoryItem): string {
  const path = entry.item?.thumbnail_path || entry.item?.file_path
  return path ? getFileUrl(path) : ''
}

/** 点动态条目跳素材详情（素材已彻底删除时不给跳） */
function openHistoryItem(entry: FavoriteHistoryItem): void {
  if (!entry.item?.id) return
  openInspiration(router, entry.item.id, {})
}

onMounted(() => {
  void loadOverview()
  void loadHistory()
})
</script>

<template>
  <div class="favorites-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">收藏中心</h2>
        <p class="page-subtitle">
          收藏行为集中看：总收藏 / 本周新增 / 未评分 / 未整理，以及最近收藏与收藏动态。
          「未整理」指还没有归入任何合集的收藏。
        </p>
      </div>
    </div>

    <!-- 概览：统计卡 + 最近收藏 -->
    <AsyncState
      :loading="overviewLoading"
      :error="overviewError"
      :empty="false"
      loading-text="正在加载收藏概览..."
      @retry="loadOverview"
    >
      <StatCardGrid :items="statItems" :span="6" class="fav-stats" />

      <section class="fav-section">
        <div class="section-head">
          <h3>最近收藏</h3>
          <span class="section-meta">最近 {{ recent.length }} 条 · 按收藏时间倒序</span>
        </div>
        <AsyncState
          :loading="false"
          :error="null"
          :empty="recent.length === 0"
          empty-text="还没有收藏素材：在素材库/详情页点收藏后会出现在这里"
        >
          <MasonryGrid
            :items="recent"
            density="standard"
            :show-actions="false"
            :show-favorite="true"
            :pending-favorite-ids="pendingFavoriteIds"
            @toggle-favorite="handleRecentFavorite"
          />
        </AsyncState>
      </section>
    </AsyncState>

    <!-- 收藏动态：收藏/取消收藏流水 -->
    <section class="fav-section">
      <div class="section-head">
        <h3>收藏动态</h3>
        <span class="section-meta">最近 {{ history.length }} 条</span>
      </div>
      <AsyncState
        :loading="historyLoading"
        :error="historyError"
        :empty="history.length === 0"
        empty-text="还没有收藏动态：收藏/取消收藏后会在这里留痕"
        loading-text="正在加载收藏动态..."
        @retry="loadHistory"
      >
        <ul class="fav-history">
          <li
            v-for="entry in history"
            :key="entry.id"
            class="fav-history-item"
            :class="{ 'has-item': !!entry.item }"
            @click="openHistoryItem(entry)"
          >
            <div class="fav-thumb">
              <img
                v-if="historyThumbUrl(entry)"
                :src="historyThumbUrl(entry)"
                alt=""
                loading="lazy"
              />
              <span v-else class="fav-thumb-empty">无图</span>
            </div>
            <div class="fav-history-main">
              <div class="fav-history-title">
                <a-tag
                  :color="isUnfavoriteAction(entry.action) ? 'gray' : 'green'"
                  size="small"
                  class="fav-action-tag"
                  >{{ favoriteActionLabel(entry.action) }}</a-tag
                >
                <span class="fav-history-author">
                  {{ entry.item?.source_author ? `@${entry.item.source_author}` : '未知作者' }}
                </span>
                <span class="fav-history-source">{{ favoriteSourceLabel(entry.source) }}</span>
              </div>
              <div class="fav-history-times">
                <span class="fav-time">{{ formatDateTime(entry.created_at) }}</span>
                <span v-if="entry.item?.favorited_at" class="fav-time">
                  收藏于 {{ formatDateTime(entry.item.favorited_at) }}
                </span>
              </div>
            </div>
            <span class="fav-history-arrow" aria-hidden="true">→</span>
          </li>
        </ul>
      </AsyncState>
    </section>
  </div>
</template>

<style scoped>
/* 印样台：收藏中心 = 统计卡 + 最近收藏印样 + 收藏流水 */
.favorites-page {
  max-width: 1680px;
  margin: 0 auto;
  padding-bottom: 32px;
}

.page-header {
  align-items: flex-end;
  padding-bottom: 16px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.page-subtitle {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-2);
  max-width: 52em;
}

.fav-section {
  margin-top: 22px;
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}

.section-head h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
}

.section-meta {
  font-size: 12px;
  color: var(--ink-3);
}

/* 收藏动态：一行一条流水（缩略图 + 动作/来源 + 时间），时间单行不换行 */
.fav-history {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--sheet);
  overflow: hidden;
}

.fav-history-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}

.fav-history-item:last-child {
  border-bottom: 0;
}

.fav-history-item.has-item {
  cursor: pointer;
}

.fav-history-item.has-item:hover {
  background: var(--paper);
}

.fav-thumb {
  width: 56px;
  height: 72px;
  flex-shrink: 0;
  overflow: hidden;
  background: var(--sheet-2);
  border: 1px solid var(--line);
}

.fav-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.fav-thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 11px;
  color: var(--ink-3);
}

.fav-history-main {
  flex: 1;
  min-width: 0;
}

.fav-history-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.fav-action-tag {
  flex-shrink: 0;
}

.fav-history-author {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 22em;
}

.fav-history-source {
  font-size: 11px;
  color: var(--ink-3);
}

.fav-history-times {
  display: flex;
  gap: 14px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.fav-time {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 12px;
  color: var(--ink-3);
  white-space: nowrap;
}

.fav-history-arrow {
  color: var(--ink-3);
  flex-shrink: 0;
}

/* 375：统计卡 2×2，动态条目允许换行，时间仍单行 */
@media (max-width: 767px) {
  .fav-stats :deep(.arco-col) {
    flex: 0 0 50%;
    max-width: 50%;
  }

  .fav-history-item {
    align-items: flex-start;
  }

  .fav-history-author {
    max-width: 100%;
  }

  .fav-history-arrow {
    display: none;
  }
}
</style>
