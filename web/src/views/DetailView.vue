<script setup lang="ts">
/** 素材详情页：大图浏览、标签编辑、收藏、删除、相似推荐。 */

import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  fetchInspiration,
  toggleFavorite,
  deleteInspiration,
  getFileUrl,
  addTagsToInspiration,
  removeTagFromInspiration,
  suggestOutfitTags,
  type InspirationDetailOut,
} from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

/** 素材详情数据 */
const detail = ref<InspirationDetailOut | null>(null)
/** 灯箱是否打开 */
const lightboxOpen = ref(false)
/** 正在加载 */
const loading = ref(true)

onMounted(async () => {
  try {
    detail.value = await fetchInspiration(route.params.id as string)
  } catch {
    message.error('加载素材详情失败')
  } finally {
    loading.value = false
  }
})

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

/** 标签类别颜色映射 */
function tagColor(category: string): string {
  const colors: Record<string, string> = {
    style: '#8b5cf6',
    item_type: '#3b82f6',
    color: '#f59e0b',
    body_part: '#10b981',
    fit: '#ec4899',
    occasion: '#06b6d4',
    attribute: '#6b7280',
    free: '#9ca3af',
    outfit: '#e11d48',
  }
  return colors[category] || '#9ca3af'
}

/** 类别中文名 */
const CAT_LABELS: Record<string, string> = {
  style: '风格', item_type: '单品', color: '颜色',
  body_part: '穿着方式', fit: '版型', occasion: '场合',
  attribute: '属性', free: '自定义', outfit: '穿搭大标签',
}

/** 按类别分组标签 */
function groupedTags() {
  if (!detail.value) return {}
  const groups: Record<string, typeof detail.value.tags> = {}
  for (const t of detail.value.tags) {
    const cat = t.tag.category
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

// ===== 穿搭大标签 =====
const outfitInput = ref('')
const outfitAdding = ref(false)
const aiSuggesting = ref(false)
const aiSuggestions = ref<string[]>([])

/** 当前素材的穿搭大标签 */
function outfitTags() {
  if (!detail.value) return []
  return detail.value.tags.filter((t) => t.tag.category === 'outfit')
}

/** 手动添加大标签 */
async function addOutfitTag() {
  const name = outfitInput.value.trim()
  if (!name || !detail.value) return
  outfitAdding.value = true
  try {
    await addTagsToInspiration(detail.value.id, [name], 'outfit', 'manual')
    outfitInput.value = ''
    message.success('已添加大标签')
    detail.value = await fetchInspiration(detail.value.id)
  } catch {
    message.error('添加失败')
  } finally {
    outfitAdding.value = false
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

/** 丢弃某条 AI 建议 */
function dismissOutfitTag(name: string) {
  aiSuggestions.value = aiSuggestions.value.filter((s) => s !== name)
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
          <!-- 左侧：大图 -->
          <div class="image-section">
            <img
              :src="getFileUrl(detail.file_path)"
              alt="穿搭素材"
              @click="lightboxOpen = true"
              class="main-image"
            />

            <!-- 大图灯箱 -->
            <ImageLightbox
              :show="lightboxOpen"
              :image-path="detail.file_path"
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
                  <a :href="detail.source_url" target="_blank">打开</a>
                </n-descriptions-item>
                <n-descriptions-item label="AI 分析">
                  <n-tag
                    size="small"
                    :type="detail.analysis_status === 'done' ? 'success' : detail.analysis_status === 'error' ? 'error' : 'default'"
                  >
                    {{ analysisStatusLabel() }}
                  </n-tag>
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
                  @close="removeOutfitTag(t.tag.id)"
                >
                  {{ t.tag.name }}
                </n-tag>
              </div>
              <div v-else style="font-size:12px;color:#999;margin-bottom:8px">暂无大标签</div>

              <div class="outfit-tag-add">
                <n-input
                  v-model:value="outfitInput"
                  size="small"
                  placeholder="手动输入大标签，如「白色系穿搭」"
                  @keyup.enter="addOutfitTag"
                  style="flex:1"
                />
                <n-button size="small" :loading="outfitAdding" @click="addOutfitTag">添加</n-button>
              </div>

              <div v-if="aiSuggestions.length" class="outfit-tag-suggestions">
                <div style="font-size:12px;color:#999;margin:8px 0 4px">AI 建议（点击标签入库，点 ✕ 丢弃）：</div>
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
                  <n-tag
                    v-for="t in tags"
                    :key="t.tag.id"
                    size="small"
                    :bordered="false"
                    :style="{ backgroundColor: tagColor(t.tag.category) + '20', color: tagColor(t.tag.category) }"
                  >
                    {{ t.tag.name }}
                    <template v-if="t.confidence < 0.8">
                      ({{ Math.round(t.confidence * 100) }}%)
                    </template>
                  </n-tag>
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

@media (max-width: 900px) {
  .detail-layout {
    flex-direction: column;
  }
  .info-section {
    width: 100%;
  }
}
</style>
