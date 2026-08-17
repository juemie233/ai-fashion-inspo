<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐与上一张/下一张导航。 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage, NIcon } from 'naive-ui'
import { ChevronBackOutline, ChevronForwardOutline, CloseOutline } from '@vicons/ionicons5'
import {
  fetchInspiration,
  fetchInspirations,
  toggleFavorite,
  moveToTrash,
  restoreInspiration,
  deleteInspiration,
  removeTagFromInspiration,
  getFileUrl,
  analyzeInspiration,
  type InspirationDetailOut,
  type InspirationOut,
  type InspirationTagOut,
} from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import CategoryTag from '@/components/inspiration/CategoryTag.vue'
import OutfitTagSection from '@/components/inspiration/OutfitTagSection.vue'
import SimilarSection from '@/components/inspiration/SimilarSection.vue'
import PersonLinkSection from '@/components/person/PersonLinkSection.vue'
import { sourceLabel } from '@/utils/sourceLabel'
import { shortenText } from '@/utils/format'
import { buildBrowseParams, storedBrowsePageSize } from '@/utils/browseQuery'
import { CATEGORY_LABELS } from '@/api/tags'
import type { PersonBrief } from '@shared/types/person'
import { useOutfitTags } from '@/composables/useOutfitTags'
import { useSimilarItems } from '@/composables/useSimilarItems'

const route = useRoute()
const router = useRouter()
const message = useMessage()

/** 素材详情数据 */
const detail = ref<InspirationDetailOut | null>(null)
/** 灯箱是否打开 */
const lightboxOpen = ref(false)
/** 正在加载 */
const loading = ref(true)
/** 重新分析提交中（防重复点击） */
const analyzing = ref(false)

// ── 穿搭大标签 composable ──
const {
  outfitTagOptions,
  outfitSelected,
  outfitAdding,
  aiSuggesting,
  aiSuggestions,
  outfitTags,
  loadOutfitOptions,
  addOutfitTags,
  removeOutfitTag,
  aiSuggestOutfitTags,
  confirmOutfitTag,
  confirmAllOutfitTags,
  dismissOutfitTag,
} = useOutfitTags(detail)

// ── 相似素材推荐 + 批量打标 composable ──
let detailSeq = 0  // 请求序号，防止参数快速切换时旧响应覆盖新数据
const {
  similarItems,
  similarLoading,
  similarSourceLabel,
  loadSimilar,
  batchMode,
  batchSelectedIds,
  batchTagNames,
  batchAdding,
  enterBatchMode,
  exitBatchMode,
  toggleSelectSimilar,
  toggleSelectAll,
  toggleFavoriteSimilar,
  deleteSimilar,
  batchAddOutfitTags,
} = useSimilarItems(detail, outfitTagOptions, outfitTags, (seq) => seq === detailSeq)

/** 灯箱可浏览图片列表：当前图 + 相似推荐中的图片（排除视频），支持灯箱左右切换 */
const lightboxPaths = computed<string[]>(() => {
  const paths: string[] = []
  if (detail.value && detail.value.media_type !== 'video' && detail.value.file_path) {
    paths.push(detail.value.file_path)
  }
  for (const item of similarItems.value) {
    const insp = item.inspiration
    if (insp.media_type !== 'video' && insp.file_path) {
      paths.push(insp.file_path)
    }
  }
  return paths
})

// ── 上一张/下一张浏览上下文 ──
// 从进入详情时携带的列表筛选 query 重建「同一次浏览」的相邻素材，
// 让用户无需回到列表即可连续刷图；当前素材不在上下文列表时隐藏导航。

const browseItems = ref<InspirationOut[]>([])
const browseTotal = ref(0)
const browsePage = ref(parseInt(route.query.page as string) || 1)
const browseLoading = ref(false)

/** 当前素材在浏览列表中的位置（不在列表中返回 -1，隐藏导航） */
const browseIndex = computed(() => {
  if (!detail.value) return -1
  return browseItems.value.findIndex((i) => i.id === detail.value!.id)
})

/** 全局位置（跨页）：(页码-1)×每页 + 页内位置 + 1 */
const browsePosition = computed(() => {
  if (browseIndex.value < 0) return 0
  return (browsePage.value - 1) * storedBrowsePageSize() + browseIndex.value + 1
})

/** 页内是否可前进/后退（跨页由 goNeighbor 翻页补齐） */
const hasPrev = computed(() => browseIndex.value > 0 || (browseIndex.value === 0 && browsePage.value > 1))
const hasNext = computed(() => {
  if (browseIndex.value < 0) return false
  const size = storedBrowsePageSize()
  return (
    browseIndex.value < browseItems.value.length - 1 ||
    browsePage.value * size < browseTotal.value
  )
})

/** 加载当前筛选条件下的列表上下文（翻页时按目标页码加载） */
async function loadBrowseContext(page: number, seq: number) {
  browseLoading.value = true
  try {
    const data = await fetchInspirations(
      buildBrowseParams(route.query as Record<string, string>, page, storedBrowsePageSize()),
    )
    if (seq !== detailSeq) return
    browseItems.value = data.items
    browseTotal.value = data.total
    browsePage.value = page
  } catch {
    if (seq === detailSeq) {
      // 上下文加载失败：静默隐藏导航，不影响详情主流程
      browseItems.value = []
    }
  } finally {
    if (seq === detailSeq) browseLoading.value = false
  }
}

/** 跳转到指定素材，保持浏览 query（页码同步更新） */
function gotoItem(id: string, page?: number) {
  const query = { ...route.query }
  if (page !== undefined) {
    if (page > 1) query.page = String(page)
    else delete query.page
  }
  router.replace({ path: `/detail/${id}`, query })
}

/** 上一张/下一张：页内移动；到页边界时自动翻页取相邻页的首/尾素材 */
async function goNeighbor(dir: 'prev' | 'next') {
  if (!detail.value || browseIndex.value < 0 || browseLoading.value) return
  const size = storedBrowsePageSize()
  if (dir === 'prev') {
    if (browseIndex.value > 0) {
      gotoItem(browseItems.value[browseIndex.value - 1].id)
    } else if (browsePage.value > 1) {
      const page = browsePage.value - 1
      try {
        const data = await fetchInspirations(
          buildBrowseParams(route.query as Record<string, string>, page, size),
        )
        if (data.items.length > 0) gotoItem(data.items[data.items.length - 1].id, page)
        else message.info('前面没有更多素材了')
      } catch {
        message.error('加载上一页失败')
      }
    }
  } else {
    if (browseIndex.value < browseItems.value.length - 1) {
      gotoItem(browseItems.value[browseIndex.value + 1].id)
    } else if (browsePage.value * size < browseTotal.value) {
      const page = browsePage.value + 1
      try {
        const data = await fetchInspirations(
          buildBrowseParams(route.query as Record<string, string>, page, size),
        )
        if (data.items.length > 0) gotoItem(data.items[0].id, page)
        else message.info('后面没有更多素材了')
      } catch {
        message.error('加载下一页失败')
      }
    }
  }
}

/** 键盘左右键切换相邻素材（灯箱打开、输入聚焦或浏览上下文缺失时禁用） */
function onKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  const tag = target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
  if (lightboxOpen.value) return
  if (e.key === 'ArrowLeft' && hasPrev.value) {
    e.preventDefault()
    goNeighbor('prev')
  } else if (e.key === 'ArrowRight' && hasNext.value) {
    e.preventDefault()
    goNeighbor('next')
  }
}

/** 加载素材详情数据（含相似推荐与浏览上下文），路由参数变化时复用 */
async function loadDetail(id: string) {
  const seq = ++detailSeq
  loading.value = true
  detail.value = null  // 清理旧素材，避免参数切换时残留上一份内容
  lightboxOpen.value = false
  similarItems.value = []
  browseItems.value = []
  try {
    const data = await fetchInspiration(id)
    if (seq !== detailSeq) return  // 已有更新的请求，丢弃过期响应
    detail.value = data
    loadSimilar(data.id, seq)
    // 同步加载浏览上下文（翻页导航后 route.query.page 已更新）
    browsePage.value = parseInt(route.query.page as string) || 1
    loadBrowseContext(browsePage.value, seq)
  } catch {
    if (seq !== detailSeq) return
    message.error('加载素材详情失败')
  } finally {
    if (seq === detailSeq) loading.value = false
  }
}

onMounted(() => {
  loadOutfitOptions()
  loadDetail(route.params.id as string)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
})

// 详情页跳转相似推荐等场景下，Vue Router 复用同一路由记录、不会重触发 onMounted，
// 需监听参数变化重新加载数据
watch(
  () => route.params.id as string,
  (id) => {
    if (id) loadDetail(id)
  }
)

/** 切换收藏 */
async function handleToggleFavorite() {
  if (!detail.value) return
  try {
    const newState = !detail.value.is_favorite
    await toggleFavorite(detail.value.id, newState)
    detail.value.is_favorite = newState
  } catch {
    message.error('操作失败')
  }
}

/** 返回素材库，携带进入详情时的筛选 query，保证删除/返回后筛选状态不丢失 */
function goHome() {
  router.push({ path: '/', query: route.query })
}

/** 移入垃圾桶（软删除） */
async function handleDelete() {
  if (!detail.value) return
  try {
    await moveToTrash(detail.value.id)
    message.success('已移入垃圾桶')
    goHome()
  } catch {
    message.error('操作失败')
  }
}

/** 从垃圾桶恢复 */
async function handleRestore() {
  if (!detail.value) return
  try {
    const restored = await restoreInspiration(detail.value.id)
    message.success('已恢复')
    detail.value.deleted_at = restored.deleted_at ?? null
    detail.value.trash_reason = restored.trash_reason ?? null
  } catch {
    message.error('恢复失败')
  }
}

/** 彻底删除（物理删除，不可恢复） */
async function handlePermanentDelete() {
  if (!detail.value) return
  try {
    await deleteInspiration(detail.value.id)
    message.success('已彻底删除')
    goHome()
  } catch {
    message.error('删除失败')
  }
}

/** 类别中文名（复用 api/tags 的 CATEGORY_LABELS，单一来源） */
const CAT_LABELS = CATEGORY_LABELS

/** 按类别分组标签 */
function groupedTags() {
  if (!detail.value) return {}
  const groups: Record<string, typeof detail.value.tags> = {}
  for (const t of detail.value.tags) {
    const cat = t.tag.category
    if (cat === 'outfit') continue  // 穿搭大标签单独在顶部展示，避免重复渲染
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(t)
  }
  return groups
}

/** 分析状态文本 */
function analysisStatusLabel(): string {
  if (!detail.value) return ''
  if (detail.value.analysis_status === 'none') return '尚未分析'
  if (detail.value.analysis_status === 'analyzing') return '分析中...'
  if (detail.value.analysis_status === 'error') return '分析失败'
  return '已分析'
}

/** 下载原图的文件名（取文件路径最后一段） */
const downloadFileName = computed(() => {
  if (!detail.value) return 'download'
  return detail.value.file_path.split('/').pop() || 'download'
})

/** 判断「原始链接」是否为可访问的页面链接（排除图片/视频 CDN 直链与危险协议） */
const isSourceLinkValid = computed(() => {
  const url = detail.value?.source_url
  if (!url) return false
  try {
    const parsed = new URL(url)
    // 仅允许 http/https，杜绝 javascript:/data: 等被点击执行（XSS）
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
    const host = parsed.hostname.toLowerCase()
    // 图片/视频 CDN 直链直接打开会被防盗链拦截，不作为「原始链接」展示
    const cdnHosts = ['xhscdn.com', 'douyinpic.com', 'douyinvod.com', 'pstatp.com', 'snssdk.com', 'ixigua.com']
    return !cdnHosts.some((h) => host === h || host.endsWith('.' + h))
  } catch {
    return false
  }
})

/** 复制原始链接到剪贴板 */
async function copySourceUrl() {
  if (!detail.value?.source_url) return
  try {
    await navigator.clipboard.writeText(detail.value.source_url)
    message.success('已复制原始链接')
  } catch {
    message.error('复制失败')
  }
}

/** 重新触发 AI 分析（分析失败/未分析时可重试） */
async function reanalyze() {
  if (!detail.value || analyzing.value) return
  analyzing.value = true
  try {
    await analyzeInspiration(detail.value.id)
    message.success('已提交重新分析')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '重新分析失败')
  } finally {
    analyzing.value = false
  }
}

/** 更新素材详情中的人物关联列表 */
function updatePersons(list: PersonBrief[]) {
  if (detail.value) detail.value.persons = list
}

/** 点击标签跳转到搜索页 */
function goSearchByTag(name: string) {
  router.push({ path: '/search', query: { q: name } })
}

/** 移除普通标签（穿搭大标签由 OutfitTagSection 管理，不在此渲染） */
async function removeTag(t: InspirationTagOut) {
  if (!detail.value) return
  try {
    await removeTagFromInspiration(detail.value.id, t.tag.id)
    detail.value.tags = detail.value.tags.filter((x) => x.tag.id !== t.tag.id)
    message.success('已移除标签')
  } catch {
    message.error('移除标签失败')
  }
}
</script>

<template>
  <div class="detail-page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 面包屑 + 上一张/下一张导航 -->
        <div class="detail-topbar">
          <n-breadcrumb>
            <n-breadcrumb-item @click="goHome()">素材库</n-breadcrumb-item>
            <n-breadcrumb-item>素材详情</n-breadcrumb-item>
          </n-breadcrumb>
          <div v-if="browseIndex >= 0" class="browse-nav">
            <span class="browse-position">
              {{ browsePosition }} / {{ browseTotal }}
            </span>
            <n-button-group size="tiny">
              <n-button
                :disabled="!hasPrev"
                :loading="browseLoading"
                title="上一张（←）"
                @click="goNeighbor('prev')"
              >
                <template #icon><n-icon><ChevronBackOutline /></n-icon></template>
                上一张
              </n-button>
              <n-button
                :disabled="!hasNext"
                :loading="browseLoading"
                title="下一张（→）"
                @click="goNeighbor('next')"
              >
                下一张
                <template #icon><n-icon><ChevronForwardOutline /></n-icon></template>
              </n-button>
            </n-button-group>
          </div>
        </div>

        <!-- 垃圾桶提示（软删除素材） -->
        <n-alert
          v-if="detail.deleted_at"
          type="warning"
          title="此素材在垃圾桶中"
          style="margin-bottom: 16px"
        >
          删除来源：{{ detail.trash_source === 'auto' ? '自动移动（质量审核）' : '手动移入' }}；原因：{{ detail.trash_reason || '未知' }}{{ detail.quality_reason ? `（${shortenText(detail.quality_reason)}）` : '' }}；可在右侧操作区点击「恢复」移回素材库，或「彻底删除」永久移除。
        </n-alert>

        <div class="detail-layout">
          <!-- 左侧：大图 / 视频 -->
          <div class="image-section">
            <video
              v-if="detail.media_type === 'video'"
              :src="getFileUrl(detail.file_path)"
              controls
              playsinline
              class="main-image"
            />
            <img
              v-else
              :src="getFileUrl(detail.file_path)"
              alt="穿搭素材"
              @click="lightboxOpen = true"
              class="main-image"
            />

            <!-- 大图灯箱（仅图片，可左右切换到相似推荐图） -->
            <ImageLightbox
              v-if="detail.media_type !== 'video'"
              :show="lightboxOpen"
              :image-paths="lightboxPaths"
              :initial-index="0"
              @close="lightboxOpen = false"
            />
          </div>

          <!-- 右侧：信息和标签 -->
          <div class="info-section">
            <!-- 顶部操作 -->
            <div class="info-actions">
              <n-button
                :type="detail.is_favorite ? 'error' : 'default'"
                @click="handleToggleFavorite"
              >
                {{ detail.is_favorite ? '❤️ 已收藏' : '🤍 收藏' }}
              </n-button>
              <a
                :href="getFileUrl(detail.file_path)"
                :download="downloadFileName"
                class="download-link"
              >
                <n-button>⬇️ {{ detail.media_type === 'video' ? '下载视频' : '下载原图' }}</n-button>
              </a>
              <template v-if="detail.deleted_at">
                <n-button type="primary" secondary @click="handleRestore">恢复</n-button>
                <n-popconfirm @positive-click="handlePermanentDelete">
                  <template #trigger>
                    <n-button type="error">彻底删除</n-button>
                  </template>
                  彻底删除后不可恢复，确定继续？
                </n-popconfirm>
              </template>
              <n-popconfirm v-else @positive-click="handleDelete">
                <template #trigger>
                  <n-button type="error" secondary>移入垃圾桶</n-button>
                </template>
                移入垃圾桶后可在保留期内从「素材管理 → 垃圾桶」恢复
              </n-popconfirm>
            </div>

            <!-- 基本信息 -->
            <div class="info-meta">
              <n-descriptions :column="1" label-placement="left" size="small" bordered>
                <n-descriptions-item label="来源">
                  <n-tag size="small" type="info">{{ sourceLabel(detail.source_type || '') }}</n-tag>
                </n-descriptions-item>
                <n-descriptions-item v-if="detail.source_author" label="作者">
                  {{ detail.source_author }}
                </n-descriptions-item>
                <n-descriptions-item v-if="detail.source_url" label="原始链接">
                  <a v-if="isSourceLinkValid" :href="detail.source_url" target="_blank" rel="noopener noreferrer">打开</a>
                  <n-text v-else depth="3">图片直链，无法打开</n-text>
                  <n-button size="tiny" quaternary style="margin-left:8px" @click="copySourceUrl">复制</n-button>
                </n-descriptions-item>
                <n-descriptions-item label="AI 分析">
                  <n-tag
                    size="small"
                    :type="detail.analysis_status === 'done' ? 'success' : detail.analysis_status === 'error' ? 'error' : 'default'"
                  >
                    {{ analysisStatusLabel() }}
                  </n-tag>
                  <n-button
                    v-if="detail.analysis_status === 'error' || detail.analysis_status === 'none'"
                    size="tiny"
                    quaternary
                    :loading="analyzing"
                    style="margin-left:8px"
                    @click="reanalyze"
                  >重新分析</n-button>
                </n-descriptions-item>
                <n-descriptions-item label="上传时间">
                  {{ new Date(detail.created_at).toLocaleString('zh-CN') }}
                </n-descriptions-item>
              </n-descriptions>
            </div>

            <!-- 关联人物（搜索添加 / 解除关联） -->
            <PersonLinkSection
              :persons="detail.persons || []"
              :inspiration-id="detail.id"
              @change="updatePersons"
            />

            <!-- 穿搭大标签 -->
            <OutfitTagSection
              :tags="outfitTags()"
              :options="outfitTagOptions"
              v-model:selected="outfitSelected"
              :adding="outfitAdding"
              :ai-suggesting="aiSuggesting"
              :ai-suggestions="aiSuggestions"
              @add="addOutfitTags"
              @remove="removeOutfitTag"
              @tag-click="goSearchByTag"
              @ai-suggest="aiSuggestOutfitTags"
              @confirm="confirmOutfitTag"
              @confirm-all="confirmAllOutfitTags"
              @dismiss="dismissOutfitTag"
            />

            <!-- 标签分组 -->
            <div v-if="detail.tags.length > 0" class="tags-section">
              <h4>标签</h4>
              <div
                v-for="(tags, category) in groupedTags()"
                :key="category"
                class="tag-group"
              >
                <span class="tag-category-label">
                  {{ CAT_LABELS[category] || category }}
                </span>
                <div class="tag-chips">
                  <span
                    v-for="t in tags"
                    :key="t.tag.id"
                    class="tag-clickable"
                    @click="goSearchByTag(t.tag.name)"
                  >
                    <CategoryTag
                      :category="t.tag.category"
                      size="small"
                    >
                      {{ t.tag.name }}<template v-if="t.confidence < 0.8"> ({{ Math.round(t.confidence * 100) }}%)</template>
                    </CategoryTag>
                    <n-button
                      size="tiny"
                      quaternary
                      circle
                      class="tag-remove-btn"
                      title="移除该标签"
                      @click.stop="removeTag(t)"
                    >
                      <template #icon><n-icon><CloseOutline /></n-icon></template>
                    </n-button>
                  </span>
                </div>
              </div>
            </div>

            <!-- 无标签 -->
            <n-empty
              v-else
              description="暂无标签，AI 分析后会自动生成"
              size="small"
            />
          </div>
        </div>

        <!-- 相似素材推荐 -->
        <SimilarSection
          :items="similarItems"
          :loading="similarLoading"
          :batch-mode="batchMode"
          :batch-selected-ids="batchSelectedIds"
          v-model:batch-tag-names="batchTagNames"
          :batch-adding="batchAdding"
          :options="outfitTagOptions"
          :similar-source-label="similarSourceLabel"
          @enter-batch="enterBatchMode"
          @exit-batch="exitBatchMode"
          @toggle-select-all="toggleSelectAll"
          @toggle-select="toggleSelectSimilar"
          @toggle-favorite="toggleFavoriteSimilar"
          @delete="deleteSimilar"
          @batch-add="batchAddOutfitTags"
        />
      </template>
    </n-spin>
  </div>
</template>

<style scoped>
.detail-page {
  max-width: 1400px;
  margin: 0 auto;
}

.detail-layout {
  display: flex;
  gap: 24px;
}

/* 顶部：面包屑 + 浏览导航 */
.detail-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.browse-nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.browse-position {
  font-size: 13px;
  color: #999;
}

.image-section {
  flex: 1;
  min-width: 0;
}

.main-image {
  width: 100%;
  border-radius: 12px;
  cursor: zoom-in;
  object-fit: contain;
  max-height: 85vh;
  background: #f5f5f5;
}

.info-section {
  width: 360px;
  flex-shrink: 0;
}

.info-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.info-meta {
  margin-bottom: 24px;
}

.tags-section h4 {
  margin-bottom: 12px;
  font-size: 16px;
}

.tag-group {
  margin-bottom: 12px;
}

.tag-category-label {
  font-size: 12px;
  color: #999;
  display: block;
  margin-bottom: 4px;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 可点击跳转搜索的标签 */
.tag-clickable {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

/* 标签移除按钮：悬停标签时出现，避免常驻造成视觉噪音 */
.tag-remove-btn {
  opacity: 0;
  transition: opacity 0.15s;
  transform: scale(0.85);
}
.tag-clickable:hover .tag-remove-btn {
  opacity: 1;
}

/* 下载原图按钮的链接容器 */
.download-link {
  display: inline-flex;
}

@media (max-width: 900px) {
  .detail-layout {
    flex-direction: column;
  }
  .info-section {
    width: 100%;
  }
}
</style>
