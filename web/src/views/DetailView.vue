<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐与上一张/下一张导航。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { IconLeft, IconRight } from '@arco-design/web-vue/es/icon'
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
  type InspirationDetailOut,
  type InspirationTagOut,
  type TrashReason,
} from '@/api/inspirations'
import CollectionPickerModal from '@/components/collection/CollectionPickerModal.vue'
import ImageCropModal from '@/components/inspiration/ImageCropModal.vue'
import TagCorrectionModal from '@/components/inspiration/TagCorrectionModal.vue'
import SimilarSection from '@/components/inspiration/SimilarSection.vue'
import DetailInfoPanel from '@/components/inspiration/DetailInfoPanel.vue'
import DetailMediaPane from '@/components/inspiration/DetailMediaPane.vue'
import DetailTrashModal from '@/components/inspiration/DetailTrashModal.vue'
import { shortenText } from '@/utils/format'
import { CATEGORY_LABELS } from '@/constants/tag'
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
/** 是否存在已确认（锁定）人脸：由 FaceDetectionSection 上报，锁定「穿搭博主/职业模特」关联栏 */
const faceLocked = ref(false)

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

/** 加入合集弹窗 */
const collectionPickerOpen = ref(false)

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
    // analyzeInspiration 返回的是响应体本身（{message, status, ollama_will_start}），
    // 不是 axios 响应对象——不能再用 { data } 解构一层
    const data = await analyzeInspiration(detail.value.id)
    if (data.ollama_will_start) {
      Message.warning(data.message || 'Ollama 正在启动中')
    } else {
      Message.success('已提交重新分析')
    }
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

// ── AI 打标纠错反馈（「标错了」/「补充漏标」）──
const correctionVisible = ref(false)
/** 反馈模式：wrong=已有标签标错；missing=AI 漏标补充 */
const correctionMode = ref<'wrong' | 'missing'>('wrong')
/** wrong 模式下被反馈的标签 */
const correctionTag = ref<InspirationTagOut | null>(null)

/** 打开「标错了」弹窗（针对某个 AI 标签） */
function openTagCorrection(t: InspirationTagOut) {
  correctionTag.value = t
  correctionMode.value = 'wrong'
  correctionVisible.value = true
}

/** 打开「补充漏标」弹窗 */
function openMissingCorrection() {
  correctionTag.value = null
  correctionMode.value = 'missing'
  correctionVisible.value = true
}

/** 反馈提交成功：刷新详情（多标/漏标会改动标签关联） */
async function onCorrectionRecorded(result: { applied: boolean }) {
  if (!result.applied || !detail.value) return
  try {
    detail.value = await fetchInspiration(detail.value.id)
  } catch {
    /* 刷新失败不影响反馈结果提示，下次进入详情页会重新加载 */
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
          <DetailMediaPane
            v-model:lightbox-open="lightboxOpen"
            v-model:crop-open="cropOpen"
            :detail="detail"
            :main-image-src="mainImageSrc"
            :lightbox-paths="lightboxPaths"
            :file-version="fileVersion"
          />

          <!-- 右侧：信息和标签 -->
          <DetailInfoPanel
            v-model:outfit-selected="outfitSelected"
            :detail="detail"
            :face-locked="faceLocked"
            :analyzing="analyzing"
            :download-file-name="downloadFileName"
            :source-link-valid="isSourceLinkValid"
            :outfit-tags="outfitTags()"
            :outfit-options="outfitTagOptions"
            :outfit-adding="outfitAdding"
            :ai-suggesting="aiSuggesting"
            :ai-suggestions="aiSuggestions"
            @favorite="handleToggleFavorite"
            @rate="handleRate"
            @open-collection="collectionPickerOpen = true"
            @trash="openTrashModal"
            @restore="handleRestore"
            @permanent-delete="handlePermanentDelete"
            @copy-source-url="copySourceUrl"
            @reanalyze="reanalyze"
            @update-bloggers="updateBloggers"
            @update-models="updateModels"
            @lock-change="(v: boolean) => (faceLocked = v)"
            @add-outfit-tags="addOutfitTags"
            @remove-outfit-tag="removeOutfitTag"
            @tag-click="goSearchByTag"
            @ai-suggest-outfit-tags="aiSuggestOutfitTags"
            @confirm-outfit-tag="confirmOutfitTag"
            @confirm-all-outfit-tags="confirmAllOutfitTags"
            @dismiss-outfit-tag="dismissOutfitTag"
            @open-missing-correction="openMissingCorrection"
            @open-tag-correction="openTagCorrection"
            @remove-tag="removeTag"
          />
        </div>

        <!-- AI 打标纠错反馈弹窗（标错了 / 补充漏标） -->
        <TagCorrectionModal
          v-model:visible="correctionVisible"
          :inspiration-id="detail.id"
          :mode="correctionMode"
          :tag-name="correctionTag?.tag.name"
          :tag-category="
            CATEGORY_LABELS[correctionTag?.tag.category || ''] || correctionTag?.tag.category
          "
          @recorded="onCorrectionRecorded"
        />

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
    <DetailTrashModal
      v-model:visible="trashModalOpen"
      v-model:reason="trashReason"
      :submitting="trashSubmitting"
      @confirm="confirmTrash"
    />

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

  <!-- 加入合集 -->
  <CollectionPickerModal
    v-model:visible="collectionPickerOpen"
    :inspiration-ids="detail ? [detail.id] : []"
  />
</template>

<style scoped>
/* 左栏/右栏/垃圾桶弹窗的样式随标记迁到对应子组件的 scoped 块（scoped 不跨组件生效）；
   这里只留视图自身布局（页面容器 / 两栏布局 / 顶部导航 / 面包屑）。 */
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

@media (max-width: 900px) {
  .detail-layout {
    flex-direction: column;
  }
}
</style>
