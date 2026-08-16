<script setup lang="ts">
/** 人物详情页：头部信息（含内容类型徽标）+ 风格画像 + 素材瀑布流。
 *
 * UI 区分：头部以「职业模特 / 穿搭博主」徽标明确标识内容类型，
 * 素材区复用 MasonryGrid / ImageLightbox，不重写照片浏览。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  fetchPerson,
  fetchPersonInspirations,
  deletePerson,
  type PersonInspiration,
} from '@/api/persons'
import { getFileUrl, type InspirationOut } from '@/api/inspirations'
import type { PersonDetail } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import PersonTypeTag from '@/components/person/PersonTypeTag.vue'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const personId = computed(() => Number(route.params.id))

const detail = ref<PersonDetail | null>(null)
const loading = ref(true)

// ── 素材列表状态 ──
const items = ref<PersonInspiration[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const itemsLoading = ref(false)
/** 灯箱是否打开（全屏浏览该人物全部图片素材） */
const lightboxOpen = ref(false)

/** 素材转 InspirationOut（复用 MasonryGrid） */
function toInspirationOut(item: PersonInspiration): InspirationOut {
  return {
    id: item.inspiration_id,
    source_type: '',
    file_path: item.file_path,
    thumbnail_path: item.thumbnail_path,
    media_type: item.media_type,
    is_favorite: false,
    created_at: item.created_at || '',
    tags: [],
    analysis_status: 'none',
  }
}

/** 灯箱图片列表：该人物当前分页内的图片素材 */
const lightboxPaths = computed<string[]>(() =>
  items.value
    .filter((i) => i.media_type !== 'video' && i.file_path)
    .map((i) => i.file_path)
)

async function loadDetail() {
  loading.value = true
  try {
    detail.value = await fetchPerson(personId.value)
  } catch {
    message.error('加载人物详情失败')
    return
  } finally {
    loading.value = false
  }
  await loadInspirations()
}

async function loadInspirations() {
  itemsLoading.value = true
  try {
    const data = await fetchPersonInspirations(personId.value, page.value, pageSize)
    items.value = data.items ?? []
    total.value = data.total ?? 0
  } catch {
    message.error('加载人物素材失败')
  } finally {
    itemsLoading.value = false
  }
}

async function setPage(p: number) {
  page.value = p
  await loadInspirations()
}

// ── 编辑 / 删除 ──
const showForm = ref(false)

async function handleDelete() {
  if (!detail.value) return
  try {
    await deletePerson(detail.value.id)
    message.success(`已删除人物「${detail.value.name}」`)
    router.push('/persons')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

/** 点击风格标签跳转搜索页 */
function goSearchByTag(name: string) {
  router.push({ path: '/search', query: { q: name } })
}

onMounted(() => {
  loadDetail()
})
</script>

<template>
  <div class="person-detail-page">
    <n-spin :show="loading">
      <template v-if="detail">
        <!-- 面包屑 -->
        <n-breadcrumb style="margin-bottom: 16px">
          <n-breadcrumb-item @click="router.push('/persons')">人物管理</n-breadcrumb-item>
          <n-breadcrumb-item>{{ detail.name }}</n-breadcrumb-item>
        </n-breadcrumb>

        <!-- 头部信息卡 -->
        <n-card size="small" class="header-card">
          <div class="header-row">
            <div class="avatar-wrap">
              <img
                v-if="detail.avatar_path"
                :src="getFileUrl(detail.avatar_path)"
                class="avatar-img"
                :alt="detail.name"
              />
              <span v-else class="avatar-fallback">{{ detail.name.slice(0, 1) }}</span>
            </div>

            <div class="header-info">
              <div class="name-line">
                <h2 style="margin: 0">{{ detail.name }}</h2>
                <!-- 内容类型徽标：UI 区分核心 -->
                <PersonTypeTag :type="detail.person_type" size="medium" />
              </div>
              <div class="meta-line">
                <n-tag size="small" :bordered="false" round>
                  {{ PERSON_PLATFORM_LABELS[detail.platform as keyof typeof PERSON_PLATFORM_LABELS] || detail.platform }}
                </n-tag>
                <n-text depth="3" style="font-size: 13px">
                  {{ detail.inspiration_count ?? 0 }} 条素材 · 创建于
                  {{ detail.created_at ? new Date(detail.created_at).toLocaleDateString('zh-CN') : '-' }}
                </n-text>
              </div>
              <div v-if="detail.bio" class="bio-line">
                <n-text depth="2">{{ detail.bio }}</n-text>
              </div>
              <div v-if="detail.profile_url" class="bio-line">
                <a :href="detail.profile_url" target="_blank" rel="noopener">主页链接 ↗</a>
              </div>
            </div>

            <div class="header-actions">
              <n-button secondary @click="showForm = true">编辑</n-button>
              <n-popconfirm @positive-click="handleDelete">
                <template #trigger>
                  <n-button type="error" secondary>删除</n-button>
                </template>
                确定删除人物「{{ detail.name }}」？其素材不会被删除，仅解除关联。
              </n-popconfirm>
            </div>
          </div>
        </n-card>

        <!-- 风格画像 -->
        <n-card size="small" class="profile-card" title="风格画像（基于该人物素材标签聚合）">
          <div class="profile-grid">
            <!-- 高频标签 -->
            <div class="profile-block">
              <h4>高频标签</h4>
              <div class="tag-chips">
                <template v-if="detail.style_profile.top_tags.length">
                  <span
                    v-for="t in detail.style_profile.top_tags"
                    :key="t.tag_id"
                    class="tag-chip"
                    @click="goSearchByTag(t.name)"
                  >
                    {{ t.name }}
                    <span class="tag-count">{{ t.count }}</span>
                  </span>
                </template>
                <n-empty v-else description="暂无标签数据" size="small" />
              </div>
            </div>

            <!-- 类别分布 -->
            <div class="profile-block">
              <h4>类别分布</h4>
              <div class="cat-list">
                <template v-if="Object.keys(detail.style_profile.by_category).length">
                  <div
                    v-for="(count, cat) in detail.style_profile.by_category"
                    :key="cat"
                    class="cat-row"
                  >
                    <span class="cat-name">{{ cat }}</span>
                    <span class="cat-bar"><span class="cat-fill" :style="{ width: Math.min(100, count * 8) + '%' }" /></span>
                    <span class="cat-count">{{ count }}</span>
                  </div>
                </template>
                <n-empty v-else description="暂无数据" size="small" />
              </div>
            </div>

            <!-- 趋势 -->
            <div class="profile-block">
              <h4>素材趋势（按月）</h4>
              <div class="trend-list">
                <template v-if="detail.style_profile.trend.length">
                  <div
                    v-for="t in detail.style_profile.trend.slice(-12)"
                    :key="t.bucket"
                    class="trend-row"
                  >
                    <span class="trend-bucket">{{ t.bucket }}</span>
                    <span class="trend-bar"><span class="trend-fill" :style="{ width: Math.min(100, t.count * 12) + '%' }" /></span>
                    <span class="trend-count">{{ t.count }}</span>
                  </div>
                </template>
                <n-empty v-else description="暂无趋势" size="small" />
              </div>
            </div>
          </div>
        </n-card>

        <!-- 素材瀑布流 -->
        <n-card size="small" class="items-card">
          <div class="items-header">
            <h3 style="margin: 0">TA 的素材</h3>
            <n-space>
              <n-button
                size="small"
                secondary
                :disabled="lightboxPaths.length === 0"
                @click="lightboxOpen = true"
              >
                🖼️ 全屏浏览
              </n-button>
            </n-space>
          </div>

          <MasonryGrid
            :items="items.map(toInspirationOut)"
            :loading="itemsLoading"
            empty-text="该人物还没有素材，去素材详情页关联或按博主采集吧"
          />

          <n-pagination
            v-if="total > pageSize"
            style="margin-top: 16px; justify-content: flex-end"
            :page="page"
            :page-size="pageSize"
            :item-count="total"
            @update:page="setPage"
          />
        </n-card>

        <!-- 全屏灯箱：浏览该人物图片素材 -->
        <ImageLightbox
          :show="lightboxOpen"
          :image-paths="lightboxPaths"
          :initial-index="0"
          @close="lightboxOpen = false"
        />
      </template>
    </n-spin>

    <!-- 编辑对话框 -->
    <PersonFormModal v-model:show="showForm" :person="detail" @saved="loadDetail()" />
  </div>
</template>

<style scoped>
.person-detail-page {
  max-width: 1200px;
  margin: 0 auto;
}

.header-card {
  margin-bottom: 12px;
}

.header-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.avatar-wrap {
  width: 72px;
  height: 72px;
  flex-shrink: 0;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eef1f6;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-fallback {
  font-size: 30px;
  color: #4a5a7a;
  font-weight: 600;
}

.header-info {
  flex: 1;
  min-width: 0;
}

.name-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.bio-line {
  margin-top: 4px;
}

.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* 风格画像 */
.profile-card {
  margin-bottom: 12px;
}

.profile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 20px;
}

.profile-block h4 {
  margin: 0 0 10px;
  font-size: 14px;
  color: #4b5563;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #eef4ff;
  color: #2f5bd0;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}

.tag-chip:hover {
  background: #dce8ff;
}

.tag-count {
  font-size: 11px;
  color: #8aa1c8;
}

.cat-list,
.trend-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cat-row,
.trend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.cat-name,
.trend-bucket {
  width: 72px;
  flex-shrink: 0;
  color: #4b5563;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cat-bar,
.trend-bar {
  flex: 1;
  height: 8px;
  background: #eef1f6;
  border-radius: 4px;
  overflow: hidden;
}

.cat-fill {
  display: block;
  height: 100%;
  background: #7ba7f0;
  border-radius: 4px;
}

.trend-fill {
  display: block;
  height: 100%;
  background: #9aa7f0;
  border-radius: 4px;
}

.cat-count,
.trend-count {
  width: 28px;
  text-align: right;
  color: #6b7280;
  font-size: 12px;
}

/* 素材区 */
.items-card {
  margin-bottom: 24px;
}

.items-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

@media (max-width: 900px) {
  .profile-grid {
    grid-template-columns: 1fr;
  }
}
</style>
