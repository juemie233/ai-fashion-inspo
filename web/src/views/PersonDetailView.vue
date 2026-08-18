<script setup lang="ts">
/** 人物详情页：头部信息（含内容类型徽标）+ 风格画像 + 素材瀑布流。
 *
 * UI 区分：头部以「职业模特 / 穿搭博主」徽标明确标识内容类型，
 * 素材区复用 MasonryGrid / ImageLightbox，不重写照片浏览。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  bloggersApi,
  modelsApi,
  fetchModelPhotoSets,
  fetchModelPhotoSet,
  deleteModelPhotoSet,
  type PersonInspiration,
  type ModelPhotoSet,
} from '@/api/persons'
import { getFileUrl, type InspirationOut } from '@/api/inspirations'
import type { PersonDetail, PersonType } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import PersonTypeTag from '@/components/person/PersonTypeTag.vue'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const personId = computed(() => Number(route.params.id))
/** 人物种类：由列表页跳转时携带（/persons/:id?kind=blogger|model） */
const kind = computed<PersonType>(() => (route.query.kind === 'model' ? 'model' : 'blogger'))
/** 按种类选择 API（博主 / 模特已拆分） */
const api = computed(() => (kind.value === 'model' ? modelsApi : bloggersApi))
const kindLabel = computed(() => (kind.value === 'model' ? '职业模特' : '穿搭博主'))

const detail = ref<PersonDetail | null>(null)
const loading = ref(true)

/** 主页链接是否安全可点击（仅允许 http/https，杜绝 javascript:/data: 注入） */
const isProfileUrlSafe = computed(() => {
  const url = detail.value?.profile_url
  if (!url) return false
  try {
    const p = new URL(url)
    return p.protocol === 'http:' || p.protocol === 'https:'
  } catch {
    return false
  }
})

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

// ── 照片组（模特写真：与穿搭素材分离）──
const photoSets = ref<ModelPhotoSet[]>([])
const photoSetsLoading = ref(false)
/** 照片组灯箱：浏览某个照片组的照片 */
const photoLightboxOpen = ref(false)
const photoLightboxPaths = ref<string[]>([])
const photoLightboxName = ref('')

async function loadPhotoSets() {
  photoSetsLoading.value = true
  try {
    const data = await fetchModelPhotoSets(personId.value, 1, 50)
    photoSets.value = data.items ?? []
  } catch {
    // 照片组加载失败不阻塞详情页其余内容
  } finally {
    photoSetsLoading.value = false
  }
}

/** 点击照片组：加载组内照片并打开灯箱浏览 */
async function openPhotoSet(set: ModelPhotoSet) {
  try {
    const detail = await fetchModelPhotoSet(personId.value, set.id, 1, 200)
    photoLightboxPaths.value = (detail.photos ?? []).map((p) => p.file_path)
    photoLightboxName.value = set.name
    photoLightboxOpen.value = true
  } catch {
    message.error('加载照片组失败')
  }
}

/** 删除照片组（二次确认） */
async function handleDeletePhotoSet(set: ModelPhotoSet) {
  try {
    await deleteModelPhotoSet(personId.value, set.id)
    message.success(`已删除照片组「${set.name}」`)
    await loadPhotoSets()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

/** 跳转到「添加模特照片」页并预选当前人物 */
function goAddPhotos() {
  router.push({ path: '/model-photos', query: { person_id: personId.value } })
}

async function loadDetail() {
  // 参数兜底：非法 id（NaN/非正整数）直接回列表，避免 404 误报
  const id = personId.value
  if (!Number.isInteger(id) || id <= 0) {
    message.error('人物参数无效')
    router.replace('/persons')
    return
  }
  loading.value = true
  try {
    detail.value = await api.value.fetchDetail(id)
  } catch {
    message.error('加载人物详情失败')
    return
  } finally {
    loading.value = false
  }
  await loadInspirations()
  await loadPhotoSets()
}

async function loadInspirations() {
  itemsLoading.value = true
  try {
    const data = await api.value.fetchInspirations(personId.value, page.value, pageSize)
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
    await api.value.remove(detail.value.id)
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

// 路由参数变化（未来人物间跳转 / 复用同一路由记录）时重新加载
watch(personId, () => {
  page.value = 1
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
                <PersonTypeTag :type="kind" size="medium" />
              </div>
              <div class="meta-line">
                <n-tag size="small" :bordered="false" round>
                  {{ PERSON_PLATFORM_LABELS[detail.platform] || detail.platform }}
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
                <a v-if="isProfileUrlSafe" :href="detail.profile_url" target="_blank" rel="noopener noreferrer">主页链接 ↗</a>
                <n-text v-else depth="3">主页链接：{{ detail.profile_url }}</n-text>
              </div>
            </div>

            <div class="header-actions">
              <n-button secondary @click="showForm = true">编辑</n-button>
              <n-popconfirm @positive-click="handleDelete">
                <template #trigger>
                  <n-button type="error" secondary>删除</n-button>
                </template>
                确定删除{{ kindLabel }}「{{ detail.name }}」？仅当该人物无关联素材时才可删除。
              </n-popconfirm>
            </div>
          </div>
        </n-card>

        <!-- 照片组（模特写真，仅职业模特展示；与穿搭素材分离） -->
        <n-card v-if="kind === 'model'" size="small" class="photo-sets-card">
          <div class="items-header">
            <h3 style="margin: 0">照片组（模特写真）</h3>
            <n-button size="small" type="primary" secondary @click="goAddPhotos">
              ＋ 添加照片
            </n-button>
          </div>

          <div v-if="photoSets.length > 0" class="photo-sets-grid">
            <div v-for="set in photoSets" :key="set.id" class="photo-set-card">
              <div class="photo-set-cover" @click="openPhotoSet(set)">
                <img
                  v-if="set.cover_path"
                  :src="getFileUrl(set.cover_path)"
                  :alt="set.name"
                />
                <span v-else class="cover-fallback">🖼️</span>
                <div class="photo-set-count">{{ set.photo_count }} 张</div>
              </div>
              <div class="photo-set-meta">
                <span class="photo-set-name" :title="set.name">{{ set.name }}</span>
                <n-space :size="4">
                  <n-button size="tiny" quaternary @click="openPhotoSet(set)">浏览</n-button>
                  <n-popconfirm @positive-click="handleDeletePhotoSet(set)">
                    <template #trigger>
                      <n-button size="tiny" type="error" quaternary>删除</n-button>
                    </template>
                    确定删除照片组「{{ set.name }}」？组内照片将一并删除。
                  </n-popconfirm>
                </n-space>
              </div>
            </div>
          </div>

          <n-empty
            v-else-if="!photoSetsLoading"
            description="暂无照片组，点击右上角「添加照片」从文件夹导入"
            size="small"
            style="margin: 24px 0"
          />
          <n-spin v-if="photoSetsLoading" :show="true" style="margin: 24px 0" />
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
            :show-actions="false"
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

        <!-- 全屏灯箱：浏览照片组照片 -->
        <ImageLightbox
          :show="photoLightboxOpen"
          :image-paths="photoLightboxPaths"
          :initial-index="0"
          @close="photoLightboxOpen = false"
        />
      </template>
    </n-spin>

    <!-- 编辑对话框 -->
    <PersonFormModal v-model:show="showForm" :kind="kind" :person="detail" @saved="loadDetail()" />
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

/* 照片组 */
.photo-sets-card {
  margin-bottom: 12px;
}

.photo-sets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}

.photo-set-card {
  border: 1px solid #eef1f6;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.photo-set-cover {
  position: relative;
  aspect-ratio: 3 / 4;
  cursor: pointer;
  background: #f3f5f9;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.photo-set-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  font-size: 36px;
}

.photo-set-count {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}

.photo-set-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  padding: 8px;
}

.photo-set-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .profile-grid {
    grid-template-columns: 1fr;
  }
}
</style>
