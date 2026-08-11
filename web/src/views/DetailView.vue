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
    season: '#84cc16',
    attribute: '#6b7280',
    free: '#9ca3af',
  }
  return colors[category] || '#9ca3af'
}

/** 类别中文名 */
const CAT_LABELS: Record<string, string> = {
  style: '风格', item_type: '单品', color: '颜色',
  body_part: '穿着方式', fit: '版型', occasion: '场合',
  season: '季节', attribute: '属性', free: '自定义',
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

/** 分析状态文本 */
function analysisStatusLabel(): string {
  if (!detail.value) return ''
  if (detail.value.analysis_status === 'none') return '尚未分析'
  if (detail.value.analysis_status === 'analyzing') return '分析中...'
  if (detail.value.analysis_status === 'error') return '分析失败'
  return '已分析'
}
</script>

<template>
  <div class="detail-page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 面包屑 -->
        <n-breadcrumb style="margin-bottom: 16px">
          <n-breadcrumb-item @click="router.push('/')">灵感库</n-breadcrumb-item>
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
                  <n-tag size="small" type="info">{{ detail.source_type }}</n-tag>
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

@media (max-width: 900px) {
  .detail-layout {
    flex-direction: column;
  }
  .info-section {
    width: 100%;
  }
}
</style>
