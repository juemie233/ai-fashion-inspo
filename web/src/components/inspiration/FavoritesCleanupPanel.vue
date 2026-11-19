<script setup lang="ts">
/** 收藏中心「待整理」工作台：智能视图 + 清理摘要 + 批量整理动作。
 *
 * 数据来源（后端并行契约）：
 * - GET /favorites/views：4 个智能视图（未评分/未整理/高分/收藏超 90 天）及各自数量；
 * - GET /favorites/views/{key}：视图素材分页（page/page_size）；
 * - GET /favorites/cleanup：缺失文件清单 + 重复内容组；
 * 动作：批量取消收藏、加入合集（复用 CollectionPickerModal）、重复组「保留第一张」。
 */

import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import AsyncState from '@/components/common/AsyncState.vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import CollectionPickerModal from '@/components/collection/CollectionPickerModal.vue'
import { batchFavorite, getFileUrl, type InspirationOut } from '@/api/inspirations'
import {
  fetchFavoriteViewItems,
  fetchFavoritesCleanup,
  fetchFavoriteViews,
  type FavoriteCleanupOut,
  type FavoriteDuplicateGroup,
  type FavoriteViewOut,
} from '@/api/favorites'
import { getApiErrorMessage } from '@/utils/apiError'

/** 每个视图每页加载多少张（点「加载更多」追加） */
const VIEW_PAGE_SIZE = 24

// ── 智能视图 ──
const views = ref<FavoriteViewOut[]>([])
const viewsLoading = ref(false)
const viewsError = ref<string | null>(null)

const activeViewKey = ref('')
const viewItems = ref<InspirationOut[]>([])
const viewTotal = ref(0)
const viewPage = ref(1)
const viewLoading = ref(false)
const viewError = ref<string | null>(null)

const activeView = computed(() => views.value.find((v) => v.key === activeViewKey.value) ?? null)
const activeViewLabel = computed(() => activeView.value?.label ?? '选择一个视图')
const viewHasMore = computed(() => viewItems.value.length < viewTotal.value)

// ── 清理摘要（缺失文件 / 重复组）──
const cleanup = ref<FavoriteCleanupOut | null>(null)
const cleanupLoading = ref(false)
const cleanupError = ref<string | null>(null)
/** 当前展开的摘要区块；再点一次收起 */
const expandedSection = ref<'missing' | 'duplicates' | null>(null)

const counts = computed(
  () =>
    cleanup.value?.counts ?? {
      unrated: 0,
      unfiled: 0,
      missing_files: 0,
      duplicate_groups: 0,
      duplicate_items: 0,
    },
)

// ── 多选与批量动作 ──
const selectedIds = ref<Set<string>>(new Set())
const selectedCount = computed(() => selectedIds.value.size)
const pickerVisible = ref(false)
const submitting = ref(false)

const duplicateGroup = ref<FavoriteDuplicateGroup | null>(null)
const duplicateSubmitting = ref(false)

function toggleSelect(id: string): void {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function clearSelection(): void {
  selectedIds.value = new Set()
}

/** 取消收藏一批素材（公共逻辑）：成功后刷新视图/摘要/计数 */
async function unfavoriteIds(ids: string[]): Promise<boolean> {
  if (ids.length === 0) return false
  submitting.value = true
  try {
    const updated = await batchFavorite(ids, false)
    Message.success(`已取消收藏 ${updated} 个素材`)
    return true
  } catch (e) {
    Message.error(getApiErrorMessage(e, '取消收藏失败'))
    return false
  } finally {
    submitting.value = false
  }
}

/** 动作成功后统一刷新：视图计数、清理摘要、当前视图第一页 */
async function refreshAll(): Promise<void> {
  clearSelection()
  await Promise.all([loadViews(), loadCleanup()])
  if (activeViewKey.value) await loadView(activeViewKey.value, 1)
}

async function handleBatchUnfavorite(): Promise<void> {
  const ok = await unfavoriteIds([...selectedIds.value])
  if (ok) await refreshAll()
}

function openCollectionPicker(): void {
  if (selectedIds.value.size === 0) {
    Message.warning('请先选择素材')
    return
  }
  pickerVisible.value = true
}

/** 「加入合集」成功后：未整理数量会变，刷新视图与清理摘要 */
async function onAddedToCollection(): Promise<void> {
  await refreshAll()
}

/** 缺失文件：单条取消收藏（文件已丢失，取消收藏即从书签里清掉） */
async function handleMissingUnfavorite(item: InspirationOut): Promise<void> {
  const ok = await unfavoriteIds([item.id])
  if (ok) await refreshAll()
}

function openDuplicateConfirm(group: FavoriteDuplicateGroup): void {
  duplicateGroup.value = group
}

/** 重复组：保留第一张，其余取消收藏（二次确认后执行） */
async function confirmDuplicateCleanup(): Promise<void> {
  const group = duplicateGroup.value
  if (!group) return
  const restIds = group.items.slice(1).map((item) => item.id)
  if (restIds.length === 0) return
  duplicateSubmitting.value = true
  try {
    const updated = await batchFavorite(restIds, false)
    Message.success(`已保留第一张，取消收藏 ${updated} 个重复素材`)
    duplicateGroup.value = null
    await refreshAll()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '清理重复收藏失败'))
  } finally {
    duplicateSubmitting.value = false
  }
}

// ── 加载 ──

async function loadViews(): Promise<void> {
  viewsLoading.value = true
  viewsError.value = null
  try {
    const data = await fetchFavoriteViews()
    views.value = data.views ?? []
    // 默认选中第一个视图，进来就能看到待处理内容
    if (!activeViewKey.value && views.value[0]) activeViewKey.value = views.value[0].key
  } catch (e) {
    viewsError.value = getApiErrorMessage(e, '加载智能视图失败')
    views.value = []
  } finally {
    viewsLoading.value = false
  }
}

async function loadCleanup(): Promise<void> {
  cleanupLoading.value = true
  cleanupError.value = null
  try {
    cleanup.value = await fetchFavoritesCleanup()
  } catch (e) {
    cleanupError.value = getApiErrorMessage(e, '加载清理摘要失败')
    cleanup.value = null
  } finally {
    cleanupLoading.value = false
  }
}

/** 加载某个智能视图（append=true 为「加载更多」追加） */
async function loadView(key: string, page = 1, append = false): Promise<void> {
  viewLoading.value = true
  viewError.value = null
  try {
    const data = await fetchFavoriteViewItems(key, page, VIEW_PAGE_SIZE)
    viewItems.value = append ? [...viewItems.value, ...data.items] : data.items
    viewTotal.value = data.total
    viewPage.value = data.page ?? page
  } catch (e) {
    viewError.value = getApiErrorMessage(e, '加载视图素材失败')
    if (!append) viewItems.value = []
  } finally {
    viewLoading.value = false
  }
}

function selectView(key: string): void {
  if (activeViewKey.value === key) return
  activeViewKey.value = key
  clearSelection()
  void loadView(key, 1)
}

function loadMore(): void {
  if (!activeViewKey.value || viewLoading.value || !viewHasMore.value) return
  void loadView(activeViewKey.value, viewPage.value + 1, true)
}

function reloadActiveView(): void {
  if (activeViewKey.value) void loadView(activeViewKey.value, 1)
}

function retryAll(): void {
  void loadViews()
  void loadCleanup()
  reloadActiveView()
}

function toggleSection(section: 'missing' | 'duplicates'): void {
  expandedSection.value = expandedSection.value === section ? null : section
}

/** 素材缩略图地址（缺失文件没有可读文件，仍尝试渲染缩略图；失败由 img 自身处理） */
function thumbUrl(item: InspirationOut): string {
  return getFileUrl(item.thumbnail_path || item.file_path)
}

onMounted(() => {
  void loadViews().then(() => {
    if (activeViewKey.value) void loadView(activeViewKey.value, 1)
  })
  void loadCleanup()
})
</script>

<template>
  <div class="cleanup-panel">
    <!-- 4 个智能视图卡：点击切换视图素材 -->
    <AsyncState
      :loading="viewsLoading"
      :error="viewsError"
      :empty="!viewsLoading && !viewsError && views.length === 0"
      empty-text="暂无可整理的收藏视图"
      loading-text="正在加载智能视图..."
      @retry="loadViews"
    >
      <div class="view-cards">
        <button
          v-for="view in views"
          :key="view.key"
          type="button"
          class="view-card"
          :class="{ active: activeViewKey === view.key }"
          @click="selectView(view.key)"
        >
          <span class="view-card-label">{{ view.label }}</span>
          <span class="view-card-count">{{ view.count }}</span>
          <span class="view-card-desc">{{ view.description }}</span>
        </button>
      </div>
    </AsyncState>

    <!-- 清理摘要：缺失文件 / 重复收藏 -->
    <section class="cleanup-section">
      <div class="section-head">
        <h3>清理摘要</h3>
        <span class="section-meta">缺失文件与重复收藏都可以在这里一次性处理</span>
      </div>
      <AsyncState
        :loading="cleanupLoading"
        :error="cleanupError"
        :empty="!cleanupLoading && !cleanupError && !cleanup"
        empty-text="暂无待整理内容"
        @retry="loadCleanup"
      >
        <div class="cleanup-cards">
          <button
            type="button"
            class="cleanup-card"
            :class="{ active: expandedSection === 'missing' }"
            @click="toggleSection('missing')"
          >
            <span class="cleanup-card-title">缺失文件</span>
            <span class="cleanup-card-value">{{ counts.missing_files }}</span>
            <span class="cleanup-card-hint"
              >点击{{ expandedSection === 'missing' ? '收起' : '展开' }}</span
            >
          </button>
          <button
            type="button"
            class="cleanup-card"
            :class="{ active: expandedSection === 'duplicates' }"
            @click="toggleSection('duplicates')"
          >
            <span class="cleanup-card-title">重复收藏</span>
            <span class="cleanup-card-value">
              {{ counts.duplicate_groups }} 组
              <em>· {{ counts.duplicate_items }} 个素材</em>
            </span>
            <span class="cleanup-card-hint">
              点击{{ expandedSection === 'duplicates' ? '收起' : '展开' }}
            </span>
          </button>
        </div>

        <!-- 缺失文件：逐条取消收藏 -->
        <div v-if="expandedSection === 'missing'" class="cleanup-list">
          <div v-for="item in cleanup?.missing_files ?? []" :key="item.id" class="missing-row">
            <span class="missing-thumb">
              <img v-if="item.thumbnail_path || item.file_path" :src="thumbUrl(item)" alt="" />
              <span v-else class="missing-thumb-empty">无图</span>
            </span>
            <span class="missing-meta">
              <span class="missing-author">
                {{ item.source_author ? `@${item.source_author}` : '未知作者' }}
              </span>
              <span class="missing-id">{{ item.id.slice(0, 8) }}</span>
            </span>
            <a-button
              size="mini"
              :loading="submitting"
              @click="() => void handleMissingUnfavorite(item)"
              >取消收藏</a-button
            >
          </div>
          <a-empty
            v-if="(cleanup?.missing_files ?? []).length === 0"
            description="没有缺失文件"
            style="padding: 8px 0"
          />
        </div>

        <!-- 重复收藏：每组保留第一张，其余取消收藏（二次确认） -->
        <div v-if="expandedSection === 'duplicates'" class="cleanup-list">
          <div
            v-for="group in cleanup?.duplicate_groups ?? []"
            :key="group.content_hash"
            class="duplicate-group"
          >
            <div class="duplicate-head">
              <span class="duplicate-title">重复组 · {{ group.items.length }} 个</span>
              <a-button size="mini" @click="openDuplicateConfirm(group)">
                保留第一张，其余取消收藏
              </a-button>
            </div>
            <div class="duplicate-thumbs">
              <div
                v-for="(item, index) in group.items"
                :key="item.id"
                class="duplicate-thumb"
                :class="{ kept: index === 0 }"
              >
                <img v-if="item.thumbnail_path || item.file_path" :src="thumbUrl(item)" alt="" />
                <span class="duplicate-tag">{{ index === 0 ? '保留' : '取消收藏' }}</span>
              </div>
            </div>
          </div>
          <a-empty
            v-if="(cleanup?.duplicate_groups ?? []).length === 0"
            description="没有重复收藏"
            style="padding: 8px 0"
          />
        </div>
      </AsyncState>
    </section>

    <!-- 当前视图素材：多选 + 批量取消收藏 / 加入合集 -->
    <section class="cleanup-section">
      <div class="section-head">
        <h3>{{ activeViewLabel }}</h3>
        <span class="section-meta">共 {{ viewTotal }} 个素材</span>
      </div>

      <div v-if="selectedCount > 0" class="batch-bar">
        <span>已选 {{ selectedCount }} 个</span>
        <a-button size="mini" :loading="submitting" @click="() => void handleBatchUnfavorite()">
          取消收藏
        </a-button>
        <a-button size="mini" :disabled="submitting" @click="openCollectionPicker">
          加入合集
        </a-button>
        <a-button size="mini" type="text" @click="clearSelection">清除选择</a-button>
      </div>

      <AsyncState
        :loading="viewLoading && viewItems.length === 0"
        :error="viewError"
        :empty="!viewLoading && !viewError && viewItems.length === 0"
        empty-text="这个视图已经很干净了（没有待整理素材）"
        loading-text="正在加载视图素材..."
        @retry="retryAll"
      >
        <MasonryGrid
          :items="viewItems"
          :loading="viewLoading"
          density="standard"
          selectable
          :selected-ids="selectedIds"
          :show-actions="false"
          :show-favorite="false"
          @toggle-select="toggleSelect"
        />
        <div v-if="viewHasMore" class="load-more">
          <a-button size="small" :loading="viewLoading" @click="loadMore">
            加载更多（已 {{ viewItems.length }} / {{ viewTotal }}）
          </a-button>
        </div>
      </AsyncState>
    </section>

    <!-- 重复组二次确认：保留第一张，其余取消收藏 -->
    <a-modal
      :visible="duplicateGroup !== null"
      :width="460"
      title="保留第一张，其余取消收藏？"
      @update:visible="(v: boolean) => !v && (duplicateGroup = null)"
    >
      <p class="duplicate-confirm-text">
        该重复组共 {{ duplicateGroup?.items.length ?? 0 }} 个素材。确认后保留第一张，其余
        {{ Math.max(0, (duplicateGroup?.items.length ?? 0) - 1) }}
        个将取消收藏（素材本体不会被删除）。
      </p>
      <template #footer>
        <div class="modal-footer">
          <a-button size="small" @click="duplicateGroup = null">取消</a-button>
          <a-button
            size="small"
            type="primary"
            :loading="duplicateSubmitting"
            @click="() => void confirmDuplicateCleanup()"
          >
            确认清理
          </a-button>
        </div>
      </template>
    </a-modal>

    <!-- 加入合集：复用素材库公共选择器 -->
    <CollectionPickerModal
      v-model:visible="pickerVisible"
      :inspiration-ids="[...selectedIds]"
      @added="() => void onAddedToCollection()"
    />
  </div>
</template>

<style scoped>
/* 待整理工作台：视图卡 = 待办抽屉标签，清理摘要 = 两张可展开的清理卡 */
.cleanup-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.view-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.view-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  text-align: left;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.view-card:hover {
  border-color: var(--line-strong);
}

.view-card.active {
  border-color: var(--pine);
  background: var(--paper);
}

.view-card-label {
  font-size: 13px;
  color: var(--ink-2);
}

.view-card-count {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 26px;
  line-height: 1.1;
  color: var(--ink);
}

.view-card-desc {
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink-3);
}

.cleanup-section {
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

.cleanup-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.cleanup-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  text-align: left;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
}

.cleanup-card.active {
  border-color: var(--amber);
}

.cleanup-card-title {
  font-size: 12px;
  color: var(--ink-3);
}

.cleanup-card-value {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 20px;
  color: var(--ink);
}

.cleanup-card-value em {
  font-style: normal;
  font-size: 12px;
  color: var(--ink-3);
}

.cleanup-card-hint {
  font-size: 11px;
  color: var(--ink-3);
}

.cleanup-list {
  margin-top: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--sheet);
  overflow: hidden;
}

.missing-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}

.missing-row:last-child {
  border-bottom: 0;
}

.missing-thumb {
  width: 40px;
  height: 52px;
  flex-shrink: 0;
  overflow: hidden;
  background: var(--sheet-2);
  border: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
}

.missing-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.missing-thumb-empty {
  font-size: 10px;
  color: var(--ink-3);
}

.missing-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.missing-author {
  font-size: 13px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.missing-id {
  font-size: 11px;
  color: var(--ink-3);
  font-family: var(--font-numeric);
}

.duplicate-group {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}

.duplicate-group:last-child {
  border-bottom: 0;
}

.duplicate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.duplicate-title {
  font-size: 13px;
  color: var(--ink);
}

.duplicate-thumbs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.duplicate-thumb {
  position: relative;
  width: 64px;
  height: 84px;
  overflow: hidden;
  background: var(--sheet-2);
  border: 1px solid var(--line);
}

.duplicate-thumb.kept {
  border-color: var(--pine);
}

.duplicate-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.duplicate-tag {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 1px 4px;
  font-size: 10px;
  text-align: center;
  color: var(--paper);
  background: rgb(28 32 30 / 62%);
}

.duplicate-thumb.kept .duplicate-tag {
  background: rgb(30 82 60 / 78%);
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: var(--sheet-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--ink-2);
  flex-wrap: wrap;
}

.load-more {
  display: flex;
  justify-content: center;
  margin-top: 14px;
}

.duplicate-confirm-text {
  margin: 0;
  line-height: 1.8;
  color: var(--ink-2);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 375/834：视图卡和清理卡收成两列，375 再收成一列 */
@media (max-width: 900px) {
  .view-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .view-cards,
  .cleanup-cards {
    grid-template-columns: 1fr;
  }
}
</style>
