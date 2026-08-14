<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐。 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  fetchInspiration,
  toggleFavorite,
  deleteInspiration,
  getFileUrl,
  addTagsToInspiration,
  batchAddTagsToInspirations,
  removeTagFromInspiration,
  suggestOutfitTags,
  analyzeInspiration,
  type InspirationDetailOut,
} from '@/api/inspirations'
import { fetchSimilar, type SimilarItemOut } from '@/api/search'
import { fetchTagsGrouped } from '@/api/tags'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import InspirationCard from '@/components/inspiration/InspirationCard.vue'
import CategoryTag from '@/components/inspiration/CategoryTag.vue'

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

// ── 相似素材推荐 ──
const similarItems = ref<SimilarItemOut[]>([])
const similarLoading = ref(false)

/** 相似来源中文标注 */
function similarSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    visual: '视觉相似',
    tag: '标签相似',
    hybrid: '视觉+标签',
  }
  return labels[source] || source
}

/** 加载相似素材推荐（视觉 + 标签加权） */
async function loadSimilar(id: string, seq: number) {
  similarLoading.value = true
  try {
    const data = await fetchSimilar(id, 10)
    if (seq !== detailSeq) return  // 过期响应不覆盖新数据
    similarItems.value = data.similar
  } catch {
    // 相似推荐失败不影响详情展示，静默降级
    if (seq !== detailSeq) return
    similarItems.value = []
  } finally {
    if (seq === detailSeq) similarLoading.value = false
  }
}

let detailSeq = 0  // 请求序号，防止参数快速切换时旧响应覆盖新数据

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

/** 删除 */
async function handleDelete() {
  if (!detail.value) return
  try {
    await deleteInspiration(detail.value.id)
    message.success('已删除')
    router.push('/')
  } catch {
    message.error('删除失败')
  }
}

/** 切换相似素材收藏 */
async function toggleFavoriteSimilar(id: string) {
  const item = similarItems.value.find((s) => s.inspiration.id === id)?.inspiration
  if (!item) return
  try {
    const newState = !item.is_favorite
    await toggleFavorite(id, newState)
    item.is_favorite = newState
  } catch {
    message.error('操作失败')
  }
}

/** 删除相似素材 */
async function deleteSimilar(id: string) {
  try {
    await deleteInspiration(id)
    similarItems.value = similarItems.value.filter((s) => s.inspiration.id !== id)
    message.success('已删除')
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

/** 来源类型中文映射 */
function sourceLabel(type: string): string {
  const labels: Record<string, string> = {
    xiaohongshu: '小红书',
    douyin: '抖音',
    scraper: '自动采集',
    manual_upload: '手动上传',
    browser_extension: '浏览器插件',
  }
  return labels[type] || type
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

// ===== 穿搭大标签 =====
const outfitTagOptions = ref<{ label: string; value: string }[]>([])
const outfitSelected = ref<string[]>([])
const outfitAdding = ref(false)
const aiSuggesting = ref(false)
const aiSuggestions = ref<string[]>([])

/** 当前素材的穿搭大标签 */
function outfitTags() {
  if (!detail.value) return []
  return detail.value.tags.filter((t) => t.tag.category === 'outfit')
}

/** 加载已有大标签作为选择项 */
async function loadOutfitOptions() {
  try {
    const groups = await fetchTagsGrouped()
    const outfit = groups.find((g) => g.category === 'outfit')
    outfitTagOptions.value = (outfit?.tags || []).map((t) => ({ label: t.name, value: t.name }))
  } catch { /* 静默 */ }
}

/** 手动添加大标签（可多选，可从已有标签中选择或输入新建） */
async function addOutfitTags() {
  if (!detail.value || outfitSelected.value.length === 0) return
  outfitAdding.value = true
  try {
    await addTagsToInspiration(detail.value.id, outfitSelected.value, 'outfit', 'manual')
    outfitSelected.value = []
    message.success('已添加大标签')
    detail.value = await fetchInspiration(detail.value.id)
    loadOutfitOptions()
  } catch {
    message.error('添加失败')
  } finally {
    outfitAdding.value = false
  }
}

/** 输入新大标签后按两次回车快速添加：第二次回车（输入框已空且有待添加标签）触发添加 */
function onOutfitEnter(e: KeyboardEvent) {
  const inputText = (e.target as HTMLInputElement | null)?.value?.trim() ?? ''
  if (inputText === '' && outfitSelected.value.length > 0 && !outfitAdding.value) {
    e.preventDefault()
    e.stopPropagation()
    addOutfitTags()
  }
}

/** 删除大标签 */
async function removeOutfitTag(tagId: number) {
  if (!detail.value) return
  try {
    await removeTagFromInspiration(detail.value.id, tagId)
    detail.value = await fetchInspiration(detail.value.id)
    message.success('已移除大标签')
  } catch {
    message.error('移除失败')
  }
}

/** AI 建议大标签（只建议不入库） */
async function aiSuggestOutfitTags() {
  if (!detail.value) return
  aiSuggesting.value = true
  aiSuggestions.value = []
  try {
    const data = await suggestOutfitTags(detail.value.id)
    aiSuggestions.value = data.suggestions || []
    if (aiSuggestions.value.length === 0) {
      message.info('AI 认为该穿搭不够有特色，未给出大标签建议')
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || 'AI 建议失败')
  } finally {
    aiSuggesting.value = false
  }
}

/** 确认入库某条 AI 建议 */
async function confirmOutfitTag(name: string) {
  if (!detail.value) return
  try {
    await addTagsToInspiration(detail.value.id, [name], 'outfit', 'ai_generated')
    aiSuggestions.value = aiSuggestions.value.filter((s) => s !== name)
    detail.value = await fetchInspiration(detail.value.id)
    message.success(`已添加「${name}」`)
  } catch {
    message.error('添加失败')
  }
}

/** 一键确认全部 AI 建议入库 */
async function confirmAllOutfitTags() {
  if (!detail.value || aiSuggestions.value.length === 0) return
  const names = [...aiSuggestions.value]
  try {
    await addTagsToInspiration(detail.value.id, names, 'outfit', 'ai_generated')
    aiSuggestions.value = []
    detail.value = await fetchInspiration(detail.value.id)
    message.success(`已全部入库 ${names.length} 个大标签`)
  } catch {
    message.error('批量入库失败')
  }
}

/** 丢弃某条 AI 建议 */
function dismissOutfitTag(name: string) {
  aiSuggestions.value = aiSuggestions.value.filter((s) => s !== name)
}

// ===== 相似素材批量添加大标签 =====
const batchMode = ref(false)                 // 是否处于批量选择模式
const batchSelectedIds = ref<string[]>([])   // 勾选的相似素材 ID
const batchTagNames = ref<string[]>([])      // 要批量添加的大标签（预填当前素材大标签）
const batchAdding = ref(false)

/** 进入批量模式：预填当前素材已有的大标签，清空勾选 */
function enterBatchMode() {
  batchSelectedIds.value = []
  const current = outfitTags().map((t) => t.tag.name)
  // 确保当前素材大标签在可选项中（AI 建议入库的标签可能尚未进 options）
  for (const name of current) {
    if (!outfitTagOptions.value.some((o) => o.value === name)) {
      outfitTagOptions.value.push({ label: name, value: name })
    }
  }
  batchTagNames.value = current
  batchMode.value = true
}

/** 退出批量模式 */
function exitBatchMode() {
  batchMode.value = false
  batchSelectedIds.value = []
  batchTagNames.value = []
}

/** 切换单个相似素材的勾选 */
function toggleSelectSimilar(id: string) {
  const idx = batchSelectedIds.value.indexOf(id)
  if (idx >= 0) batchSelectedIds.value.splice(idx, 1)
  else batchSelectedIds.value.push(id)
}

/** 全选 / 取消全选 */
function toggleSelectAll() {
  const allIds = similarItems.value.map((it) => it.inspiration.id)
  const allSelected = allIds.length > 0 && allIds.every((id) => batchSelectedIds.value.includes(id))
  batchSelectedIds.value = allSelected ? [] : [...allIds]
}

/** 批量添加大标签到勾选的相似素材，成功后刷新相似列表 */
async function batchAddOutfitTags() {
  if (batchSelectedIds.value.length === 0 || batchTagNames.value.length === 0) return
  batchAdding.value = true
  try {
    const { affected, not_found, skipped_existing } = await batchAddTagsToInspirations(
      batchSelectedIds.value,
      batchTagNames.value,
      'outfit',
      'manual',
    )
    // 明细提示：区分「实际新增」「素材不存在」「关联已存在」
    const parts = [`已为 ${affected} 个相似素材添加大标签`]
    if (not_found > 0) parts.push(`${not_found} 个素材不存在`)
    if (skipped_existing > 0) parts.push(`${skipped_existing} 条关联已存在`)
    message.success(parts.join('，'))
    exitBatchMode()
    if (detail.value) await refreshSimilar(detail.value.id)
  } catch {
    message.error('批量添加失败')
  } finally {
    batchAdding.value = false
  }
}

/** 刷新相似素材（批量加标签后卡片标签与相似度可能变化） */
async function refreshSimilar(id: string) {
  try {
    const data = await fetchSimilar(id, 10)
    similarItems.value = data.similar
  } catch { /* 静默降级 */ }
}
</script>

<template>
  <div class="detail-page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 面包屑 -->
        <n-breadcrumb style="margin-bottom: 16px">
          <n-breadcrumb-item @click="router.push('/')">素材库</n-breadcrumb-item>
          <n-breadcrumb-item>素材详情</n-breadcrumb-item>
        </n-breadcrumb>

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
              <n-popconfirm @positive-click="handleDelete">
                <template #trigger>
                  <n-button type="error" secondary>删除</n-button>
                </template>
                确定要删除这个素材吗？
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
            <div class="outfit-tags-section">
              <div class="outfit-tags-header">
                <h4>穿搭大标签</h4>
                <n-button size="tiny" type="primary" ghost :loading="aiSuggesting" @click="aiSuggestOutfitTags">
                  ✨ AI 生成
                </n-button>
              </div>

              <div v-if="outfitTags().length" class="tag-chips" style="margin-bottom:8px">
                <n-tag
                  v-for="t in outfitTags()"
                  :key="t.tag.id"
                  size="small"
                  type="error"
                  closable
                  class="tag-clickable"
                  @close="removeOutfitTag(t.tag.id)"
                  @click="goSearchByTag(t.tag.name)"
                >
                  {{ t.tag.name }}
                </n-tag>
              </div>
              <div v-else style="font-size:12px;color:#999;margin-bottom:8px">暂无大标签</div>

              <div class="outfit-tag-add" @keydown.enter.capture="onOutfitEnter">
                <n-select
                  v-model:value="outfitSelected"
                  multiple
                  filterable
                  tag
                  size="small"
                  placeholder="选择或输入大标签，如「白色系穿搭」"
                  :options="outfitTagOptions"
                  style="flex:1"
                />
                <n-button
                  size="small"
                  :loading="outfitAdding"
                  :disabled="outfitSelected.length === 0"
                  @click="addOutfitTags"
                >添加</n-button>
              </div>
              <div class="outfit-tag-hint">输入新标签后按两次回车即可快速添加</div>

              <div v-if="aiSuggestions.length" class="outfit-tag-suggestions">
                <div class="outfit-tag-suggestions-header">
                  <span style="font-size:12px;color:#999">AI 建议（点击标签入库，点 ✕ 丢弃）：</span>
                  <n-button
                    size="tiny"
                    type="warning"
                    secondary
                    @click="confirmAllOutfitTags"
                  >一键全部入库 ({{ aiSuggestions.length }})</n-button>
                </div>
                <div class="tag-chips">
                  <span
                    v-for="name in aiSuggestions"
                    :key="name"
                    style="display:inline-flex;align-items:center;gap:2px;margin:0 6px 4px 0"
                  >
                    <n-tag size="small" type="warning" style="cursor:pointer" @click="confirmOutfitTag(name)">
                      {{ name }}
                    </n-tag>
                    <n-button size="tiny" text type="error" @click="dismissOutfitTag(name)">✕</n-button>
                  </span>
                </div>
              </div>
            </div>

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
        <div class="similar-section">
          <div class="similar-header">
            <h4>相似素材推荐</h4>
            <n-spin v-if="similarLoading" size="small" />
            <span v-else-if="similarItems.length === 0" class="similar-empty-hint">
              暂无相似素材（需要先回填向量，或在图像向量不可用时依赖标签匹配）
            </span>
            <span v-else class="similar-count">{{ similarItems.length }} 个</span>
            <n-button
              v-if="similarItems.length > 0 && !batchMode"
              size="tiny"
              type="error"
              ghost
              style="margin-left:auto"
              @click="enterBatchMode"
            >
              批量添加大标签
            </n-button>
          </div>

          <!-- 批量添加操作栏 -->
          <div v-if="batchMode" class="batch-toolbar">
            <span class="batch-selected-count">
              已选 {{ batchSelectedIds.length }} / {{ similarItems.length }}
            </span>
            <n-button size="tiny" quaternary @click="toggleSelectAll">
              {{ batchSelectedIds.length === similarItems.length ? '取消全选' : '全选' }}
            </n-button>
            <n-select
              v-model:value="batchTagNames"
              multiple
              filterable
              tag
              size="small"
              placeholder="选择或输入大标签"
              :options="outfitTagOptions"
              style="flex:1; min-width:200px"
            />
            <n-button
              size="small"
              type="error"
              :loading="batchAdding"
              :disabled="batchSelectedIds.length === 0 || batchTagNames.length === 0"
              @click="batchAddOutfitTags"
            >
              添加（{{ batchSelectedIds.length }}）
            </n-button>
            <n-button size="small" @click="exitBatchMode">取消</n-button>
          </div>

          <div v-if="similarItems.length > 0" class="similar-grid">
            <InspirationCard
              v-for="item in similarItems"
              :key="item.inspiration.id"
              :item="item.inspiration"
              :badge="`${Math.round(item.similarity * 100)}% · ${similarSourceLabel(item.match_source)}`"
              :show-actions="!batchMode"
              :selectable="batchMode"
              :selected="batchSelectedIds.includes(item.inspiration.id)"
              @toggle-select="toggleSelectSimilar(item.inspiration.id)"
              @toggle-favorite="toggleFavoriteSimilar(item.inspiration.id)"
              @delete="deleteSimilar(item.inspiration.id)"
            />
          </div>
        </div>
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

/* AI 建议一键全部入库的操作行 */
.outfit-tag-suggestions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 8px 0 4px;
}

.similar-section {
  margin-top: 32px;
  border-top: 1px solid var(--n-border-color, #eee);
  padding-top: 16px;
}

.similar-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.similar-header h4 {
  margin: 0;
  font-size: 16px;
}

.similar-empty-hint {
  font-size: 12px;
  color: #999;
}

.similar-count {
  font-size: 12px;
  color: #999;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fef6f7;
  border: 1px solid #f0d6dc;
  border-radius: 8px;
  flex-wrap: wrap;
}

.batch-selected-count {
  font-size: 13px;
  color: #e0465e;
  font-weight: 600;
}

.similar-grid {
  column-count: 5;
  column-gap: 12px;
}
.similar-grid :deep(.card) {
  break-inside: avoid;
  margin-bottom: 12px;
}
@media (max-width: 1200px) { .similar-grid { column-count: 4; } }
@media (max-width: 900px)  { .similar-grid { column-count: 3; } }
@media (max-width: 600px)  { .similar-grid { column-count: 2; } }

.outfit-tags-section {
  border: 1px solid #f0d6dc;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 20px;
  background: #fef6f7;
}

.outfit-tags-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.outfit-tags-header h4 {
  margin: 0;
  font-size: 16px;
}

.outfit-tag-add {
  display: flex;
  gap: 6px;
}

.outfit-tag-hint {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
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
