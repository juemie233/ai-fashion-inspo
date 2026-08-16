<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐。 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  fetchInspiration,
  toggleFavorite,
  moveToTrash,
  restoreInspiration,
  deleteInspiration,
  getFileUrl,
  analyzeInspiration,
  type InspirationDetailOut,
} from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import CategoryTag from '@/components/inspiration/CategoryTag.vue'
import OutfitTagSection from '@/components/inspiration/OutfitTagSection.vue'
import SimilarSection from '@/components/inspiration/SimilarSection.vue'
import { sourceLabel } from '@/utils/sourceLabel'
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

/** 加载素材详情数据（含相似推荐），路由参数变化时复用 */
async function loadDetail(id: string) {
  const seq = ++detailSeq
  loading.value = true
  detail.value = null  // 清理旧素材，避免参数切换时残留上一份内容
  lightboxOpen.value = false
  similarItems.value = []
  try {
    const data = await fetchInspiration(id)
    if (seq !== detailSeq) return  // 已有更新的请求，丢弃过期响应
    detail.value = data
    loadSimilar(data.id, seq)
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

/** 类别中文名 */
const CAT_LABELS: Record<string, string> = {
  style: '风格', item_type: '单品', color: '颜色',
  body_part: '穿着方式', fit: '版型',
  attribute: '属性', free: '自定义', outfit: '穿搭大标签',
}

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

/** 判断「原始链接」是否为可访问的页面链接（排除图片/视频 CDN 直链） */
const isSourceLinkValid = computed(() => {
  const url = detail.value?.source_url
  if (!url) return false
  try {
    const host = new URL(url).hostname.toLowerCase()
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

/** 点击标签跳转到搜索页 */
function goSearchByTag(name: string) {
  router.push({ path: '/search', query: { q: name } })
}
</script>

<template>
  <div class="detail-page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 面包屑 -->
        <n-breadcrumb style="margin-bottom: 16px">
          <n-breadcrumb-item @click="goHome()">素材库</n-breadcrumb-item>
          <n-breadcrumb-item>素材详情</n-breadcrumb-item>
        </n-breadcrumb>

        <!-- 垃圾桶提示（软删除素材） -->
        <n-alert
          v-if="detail.deleted_at"
          type="warning"
          title="此素材在垃圾桶中"
          style="margin-bottom: 16px"
        >
          删除原因：{{ detail.trash_reason || '未知' }}；可点击右上角「恢复」移回素材库，或「彻底删除」永久移除。
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
                <n-button>⬇️ 下载原图</n-button>
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
                移入垃圾桶后 30 天内可在「素材管理 → 垃圾桶」恢复
              </n-popconfirm>
            </div>

            <!-- 基本信息 -->
            <div class="info-meta">
              <n-descriptions :column="1" label-placement="left" size="small" bordered>
                <n-descriptions-item label="来源">
                  <n-tag size="small" type="info">{{ sourceLabel(detail.source_type) }}</n-tag>
                </n-descriptions-item>
                <n-descriptions-item v-if="detail.source_author" label="作者">
                  {{ detail.source_author }}
                </n-descriptions-item>
                <n-descriptions-item v-if="detail.source_url" label="原始链接">
                  <a v-if="isSourceLinkValid" :href="detail.source_url" target="_blank">打开</a>
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
