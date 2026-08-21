<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐与上一张/下一张导航。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { IconLeft, IconRight, IconClose } from '@arco-design/web-vue/es/icon'
import {
  fetchInspiration,
  toggleFavorite,
  updateRating,
  moveToTrash,
  restoreInspiration,
  deleteInspiration,
  removeTagFromInspiration,
  getFileUrl,
  analyzeInspiration,
  TRASH_REASON_OPTIONS,
  type InspirationDetailOut,
  type InspirationTagOut,
  type TrashReason,
} from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import ImageCropModal from '@/components/inspiration/ImageCropModal.vue'
import CategoryTag from '@/components/inspiration/CategoryTag.vue'
import OutfitTagSection from '@/components/inspiration/OutfitTagSection.vue'
import SimilarSection from '@/components/inspiration/SimilarSection.vue'
import PersonLinkSection from '@/components/person/PersonLinkSection.vue'
import FaceDetectionSection from '@/components/inspiration/FaceDetectionSection.vue'
import { sourceLabel } from '@/utils/sourceLabel'
import { formatDate, shortenText } from '@/utils/format'
import { CATEGORY_LABELS } from '@/api/tags'
import type { PersonBrief } from '@shared/types/person'
import { useOutfitTags } from '@/composables/useOutfitTags'
import { useSimilarItems } from '@/composables/useSimilarItems'
import { useBrowseContext } from '@/composables/useBrowseContext'

const route = useRoute()
const router = useRouter()

/** 素材详情数据 */
const detail = ref<InspirationDetailOut | null>(null)
/** 灯箱是否打开 */
const lightboxOpen = ref(false)
/** 裁剪弹窗是否打开（仅图片素材显示入口） */
const cropOpen = ref(false)
/** 图片版本号：裁剪等原地替换图片后递增，附加 ?v= 绕过浏览器缓存 */
const fileVersion = ref('')
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
let detailSeq = 0 // 请求序号，防止参数快速切换时旧响应覆盖新数据
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

// ── 上一张/下一张浏览上下文（composable：状态 + 翻页加载 + 导航）──
const {
  browseTotal,
  browseLoading,
  browseIndex,
  browsePosition,
  hasPrev,
  hasNext,
  reset: resetBrowseContext,
  load: loadBrowseContext,
  goNeighbor,
} = useBrowseContext({ detail, route, router })

/** 键盘左右键切换相邻素材（灯箱打开、弹窗打开、输入聚焦或浏览上下文缺失时禁用） */
function onKeydown(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null
  const tag = target?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
  if (lightboxOpen.value || trashModalOpen.value || cropOpen.value) return
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
  detail.value = null // 清理旧素材，避免参数切换时残留上一份内容
  lightboxOpen.value = false
  similarItems.value = []
  resetBrowseContext()
  try {
    const data = await fetchInspiration(id)
    if (seq !== detailSeq) return // 已有更新的请求，丢弃过期响应
    detail.value = data
    loadSimilar(data.id, seq)
    // 同步加载浏览上下文（翻页导航后 route.query.page 已更新）
    loadBrowseContext()
  } catch {
    if (seq !== detailSeq) return
    Message.error('加载素材详情失败')
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
  },
)

/** 切换收藏 */
async function handleToggleFavorite() {
  if (!detail.value) return
  try {
    const newState = !detail.value.is_favorite
    await toggleFavorite(detail.value.id, newState)
    detail.value.is_favorite = newState
  } catch {
    Message.error('操作失败')
  }
}

/** 设置评分（0~5，0 清除）：同步详情数据 */
async function handleRate(value: number) {
  if (!detail.value) return
  try {
    await updateRating(detail.value.id, value)
    detail.value.rating = value
    Message.success(value > 0 ? `已评分 ${value} 星` : '已清除评分')
  } catch (e) {
    const detailMsg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    Message.error(detailMsg || '评分失败')
  }
}

/** 返回素材库，携带进入详情时的筛选 query，保证删除/返回后筛选状态不丢失 */
function goHome() {
  router.push({ path: '/', query: route.query })
}

/** 移入垃圾桶原因弹窗：是否打开 */
const trashModalOpen = ref(false)
/** 当前选中的删除原因（未选择时为 null，确认按钮禁用） */
const trashReason = ref<TrashReason | null>(null)
/** 移入垃圾桶提交中（防重复点击） */
const trashSubmitting = ref(false)

/** 打开移入垃圾桶弹窗（每次重新打开时重置原因选择） */
function openTrashModal() {
  trashReason.value = null
  trashModalOpen.value = true
}

/** 确认移入垃圾桶（携带所选原因，软删除可恢复） */
async function confirmTrash() {
  if (!detail.value || !trashReason.value) return
  trashSubmitting.value = true
  try {
    await moveToTrash(detail.value.id, trashReason.value)
    Message.success('已移入垃圾桶')
    goHome()
  } catch {
    Message.error('操作失败')
  } finally {
    trashSubmitting.value = false
  }
}

/** 从垃圾桶恢复 */
async function handleRestore() {
  if (!detail.value) return
  try {
    const restored = await restoreInspiration(detail.value.id)
    Message.success('已恢复')
    detail.value.deleted_at = restored.deleted_at ?? null
    detail.value.trash_reason = restored.trash_reason ?? null
  } catch {
    Message.error('恢复失败')
  }
}

/** 彻底删除（物理删除，不可恢复） */
async function handlePermanentDelete() {
  if (!detail.value) return
  try {
    await deleteInspiration(detail.value.id)
    Message.success('已彻底删除')
    goHome()
  } catch {
    Message.error('删除失败')
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
    if (cat === 'outfit') continue // 穿搭大标签单独在顶部展示，避免重复渲染
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

/** 主图完整 URL：裁剪等原地替换图片后附加 ?v= 版本参数，强制浏览器重新拉取新图 */
const mainImageSrc = computed(() => {
  if (!detail.value) return ''
  const base = getFileUrl(detail.value.file_path)
  return fileVersion.value ? `${base}?v=${fileVersion.value}` : base
})

/** 裁剪成功：刷新素材详情（后端已同步缩略图/哈希/主色调等派生数据） */
function handleCropSuccess() {
  fileVersion.value = String(Date.now()) // 先递增版本号，让主图/裁剪弹窗/灯箱取到新图
  cropOpen.value = false
  if (detail.value) loadDetail(detail.value.id)
}

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
    const cdnHosts = [
      'xhscdn.com',
      'douyinpic.com',
      'douyinvod.com',
      'pstatp.com',
      'snssdk.com',
      'ixigua.com',
    ]
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
    Message.success('已复制原始链接')
  } catch {
    Message.error('复制失败')
  }
}

/** 重新触发 AI 分析（分析失败/未分析时可重试） */
async function reanalyze() {
  if (!detail.value || analyzing.value) return
  analyzing.value = true
  try {
    await analyzeInspiration(detail.value.id)
    Message.success('已提交重新分析')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重新分析失败'))
  } finally {
    analyzing.value = false
  }
}

/** 更新素材详情中的博主关联列表 */
function updateBloggers(list: PersonBrief[]) {
  if (detail.value) detail.value.bloggers = list
}

/** 更新素材详情中的模特关联列表 */
function updateModels(list: PersonBrief[]) {
  if (detail.value) detail.value.models = list
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
    Message.success('已移除标签')
  } catch {
    Message.error('移除标签失败')
  }
}
</script>

<template>
  <div class="detail-page">
    <a-spin :loading="loading">
      <template v-if="detail">
        <!-- 面包屑 + 上一张/下一张导航 -->
        <div class="detail-topbar">
          <a-breadcrumb>
            <a-breadcrumb-item @click="goHome()">素材库</a-breadcrumb-item>
            <a-breadcrumb-item>素材详情</a-breadcrumb-item>
          </a-breadcrumb>
          <div v-if="browseIndex >= 0" class="browse-nav">
            <span class="browse-position"> {{ browsePosition }} / {{ browseTotal }} </span>
            <a-button-group size="mini">
              <a-button
                :disabled="!hasPrev"
                :loading="browseLoading"
                title="上一张（←）"
                @click="goNeighbor('prev')"
              >
                <template #icon><IconLeft /></template>
                上一张
              </a-button>
              <a-button
                :disabled="!hasNext"
                :loading="browseLoading"
                title="下一张（→）"
                @click="goNeighbor('next')"
              >
                下一张
                <template #icon><IconRight /></template>
              </a-button>
            </a-button-group>
          </div>
        </div>

        <!-- 垃圾桶提示（软删除素材） -->
        <a-alert
          v-if="detail.deleted_at"
          type="warning"
          title="此素材在垃圾桶中"
          style="margin-bottom: 16px"
        >
          删除来源：{{
            detail.trash_source === 'auto' ? '自动移动（质量审核）' : '手动移入'
          }}；原因：{{ detail.trash_reason || '未知'
          }}{{
            detail.quality_reason ? `（${shortenText(detail.quality_reason)}）` : ''
          }}；可在右侧操作区点击「恢复」移回素材库，或「彻底删除」永久移除。
        </a-alert>

        <div class="detail-layout">
          <!-- 左侧：大图 / 视频 -->
          <div class="image-section">
            <div v-if="detail.media_type === 'video'" class="main-image-wrap">
              <video :src="getFileUrl(detail.file_path)" controls playsinline class="main-image" />
            </div>
            <div v-else class="main-image-wrap">
              <img
                :src="mainImageSrc"
                alt="穿搭素材"
                @click="lightboxOpen = true"
                class="main-image"
              />
              <!-- 裁剪入口：仅图片素材显示（视频缩略图/非图片不显示） -->
              <a-button
                v-if="!detail.deleted_at"
                class="crop-entry-btn"
                size="small"
                @click.stop="cropOpen = true"
                title="裁剪图片（保留中间区域，裁掉上下部分）"
              >
                ✂️ 裁剪
              </a-button>
            </div>

            <!-- 大图灯箱（仅图片，可左右切换到相似推荐图） -->
            <ImageLightbox
              v-if="detail.media_type !== 'video'"
              :show="lightboxOpen"
              :image-paths="lightboxPaths"
              :initial-index="0"
              :image-version="fileVersion"
              @close="lightboxOpen = false"
            />
          </div>

          <!-- 右侧：信息和标签 -->
          <div class="info-section">
            <!-- 顶部操作 -->
            <div class="info-actions">
              <a-button
                :type="detail.is_favorite ? 'primary' : 'secondary'"
                :status="detail.is_favorite ? 'danger' : undefined"
                @click="handleToggleFavorite"
              >
                {{ detail.is_favorite ? '❤️ 已收藏' : '🤍 收藏' }}
              </a-button>
              <!-- 五星评分：仅整数，点击星设置，再点已选星清除（0 分） -->
              <div class="rating-box" title="评分（0~5，点击星设置，再点清除）">
                <a-rate
                  :model-value="detail.rating || 0"
                  allow-clear
                  @change="(v: number) => handleRate(v)"
                />
                <span v-if="(detail.rating || 0) > 0" class="rating-value">
                  {{ detail.rating || 0 }} 分
                </span>
              </div>
              <a
                :href="getFileUrl(detail.file_path)"
                :download="downloadFileName"
                class="download-link"
              >
                <a-button
                  >⬇️ {{ detail.media_type === 'video' ? '下载视频' : '下载原图' }}</a-button
                >
              </a>
              <template v-if="detail.deleted_at">
                <a-button type="secondary" @click="handleRestore">恢复</a-button>
                <a-popconfirm content="彻底删除后不可恢复，确定继续？" @ok="handlePermanentDelete">
                  <a-button type="primary" status="danger">彻底删除</a-button>
                </a-popconfirm>
              </template>
              <a-button v-else type="secondary" status="danger" @click="openTrashModal"
                >移入垃圾桶</a-button
              >
            </div>

            <!-- 基本信息 -->
            <div class="info-meta">
              <a-descriptions :column="1" size="small" bordered>
                <a-descriptions-item label="来源">
                  <a-tag size="small" color="arcoblue">{{
                    sourceLabel(detail.source_type || '')
                  }}</a-tag>
                </a-descriptions-item>
                <a-descriptions-item v-if="detail.source_author" label="作者">
                  {{ detail.source_author }}
                </a-descriptions-item>
                <a-descriptions-item v-if="detail.source_url" label="原始链接">
                  <a
                    v-if="isSourceLinkValid"
                    :href="detail.source_url"
                    target="_blank"
                    rel="noopener noreferrer"
                    >打开</a
                  >
                  <a-typography-text v-else type="secondary">图片直链，无法打开</a-typography-text>
                  <a-button size="mini" type="text" style="margin-left: 8px" @click="copySourceUrl"
                    >复制</a-button
                  >
                </a-descriptions-item>
                <a-descriptions-item label="AI 分析">
                  <a-tag
                    size="small"
                    :color="
                      detail.analysis_status === 'done'
                        ? 'green'
                        : detail.analysis_status === 'error'
                          ? 'red'
                          : 'gray'
                    "
                  >
                    {{ analysisStatusLabel() }}
                  </a-tag>
                  <a-button
                    v-if="detail.analysis_status === 'error' || detail.analysis_status === 'none'"
                    size="mini"
                    type="text"
                    :loading="analyzing"
                    style="margin-left: 8px"
                    @click="reanalyze"
                    >重新分析</a-button
                  >
                </a-descriptions-item>
                <a-descriptions-item label="上传时间">
                  {{ formatDate(detail.created_at) }}
                </a-descriptions-item>
              </a-descriptions>
            </div>

            <!-- 关联博主（从已有列表选择添加 / 解除关联） -->
            <PersonLinkSection
              kind="blogger"
              :persons="detail.bloggers || []"
              :inspiration-id="detail.id"
              @change="updateBloggers"
            />

            <!-- 关联模特（从已有列表选择添加 / 解除关联） -->
            <PersonLinkSection
              kind="model"
              :persons="detail.models || []"
              :inspiration-id="detail.id"
              @change="updateModels"
            />

            <!-- 人脸识别（博主特征库匹配） -->
            <FaceDetectionSection :inspiration-id="detail.id" />

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
              <div v-for="(tags, category) in groupedTags()" :key="category" class="tag-group">
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
                    <CategoryTag :category="t.tag.category" size="small">
                      {{ t.tag.name
                      }}<template v-if="t.confidence < 0.8">
                        ({{ Math.round(t.confidence * 100) }}%)</template
                      >
                    </CategoryTag>
                    <a-button
                      size="mini"
                      type="text"
                      circle
                      class="tag-remove-btn"
                      title="移除该标签"
                      @click.stop="removeTag(t)"
                    >
                      <template #icon><IconClose /></template>
                    </a-button>
                  </span>
                </div>
              </div>
            </div>

            <!-- 无标签 -->
            <a-empty v-else description="暂无标签，AI 分析后会自动生成" />
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
    </a-spin>

    <!-- 移入垃圾桶原因选择弹窗 -->
    <a-modal
      v-model:visible="trashModalOpen"
      title="移入垃圾桶"
      :width="420"
      :mask-closable="false"
    >
      <p class="trash-reason-tip">
        请选择移入垃圾桶的原因，移入后可在保留期内从「素材管理 → 垃圾桶」恢复：
      </p>
      <a-radio-group
        :model-value="trashReason ?? undefined"
        class="trash-reason-group"
        @change="(v: unknown) => (trashReason = (v as TrashReason | undefined) ?? null)"
      >
        <a-space direction="vertical" :size="10">
          <a-radio v-for="opt in TRASH_REASON_OPTIONS" :key="opt.value" :value="opt.value">{{
            opt.label
          }}</a-radio>
        </a-space>
      </a-radio-group>
      <template #footer>
        <div class="trash-modal-footer">
          <a-button @click="trashModalOpen = false">取消</a-button>
          <a-button
            status="danger"
            :loading="trashSubmitting"
            :disabled="!trashReason"
            @click="confirmTrash"
          >
            确认移入
          </a-button>
        </div>
      </template>
    </a-modal>

    <!-- 图片手动裁剪弹窗（仅图片素材；确认后由后端裁剪并同步派生数据） -->
    <ImageCropModal
      v-if="detail && detail.media_type === 'image'"
      :visible="cropOpen"
      :inspiration-id="detail.id"
      :image-path="detail.file_path"
      :image-version="fileVersion"
      @close="cropOpen = false"
      @success="handleCropSuccess"
    />
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

/* 主图容器：相对定位，供右上角裁剪入口按钮悬浮 */
.main-image-wrap {
  position: relative;
}

/* 裁剪入口：悬浮在图片右上角，不与图片的点击开灯箱冲突 */
.crop-entry-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
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

/* 评分控件：与收藏按钮并列，垂直居中 */
.rating-box {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
}

.rating-value {
  font-size: 12px;
  color: #b57914;
  font-weight: 600;
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

/* 移入垃圾桶原因弹窗 */
.trash-reason-tip {
  margin: 0 0 14px;
  color: #666;
  font-size: 13px;
  line-height: 1.6;
}

.trash-reason-group {
  display: block;
}

.trash-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
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
