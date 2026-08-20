<script setup lang="ts">
/** 添加模特照片页：只能选择文件夹，把一个文件夹整组导入为某个人物的「照片组」。
 *
 * 与「上传穿搭素材」（UploadView）分离：模特写真不进入素材库，不参与 AI 打标，
 * 仅按「人物 → 照片组 → 照片」浏览（见人物详情页）。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { modelsApi, createModelPhotoSet, uploadModelPhoto } from '@/api/persons'
import type { Person } from '@shared/types/person'
import { getApiErrorMessage } from '@/utils/apiError'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

const route = useRoute()
const router = useRouter()

// ── 人物选择 ──
const persons = ref<Person[]>([])
const personsLoading = ref(false)
const personId = ref<number | null>(null)
const showForm = ref(false)

async function loadPersons() {
  personsLoading.value = true
  try {
    const data = await modelsApi.fetchList({ page: 1, size: 200, sort: 'name' })
    persons.value = data.items
  } catch {
    Message.error('加载人物列表失败')
  } finally {
    personsLoading.value = false
  }
}

/** 新建人物成功后：先选中新人物再刷新列表（避免 loadPersons 失败导致选择状态丢失） */
async function onPersonCreated(p: Person) {
  personId.value = p.id
  await loadPersons()
}

const personOptions = computed(() =>
  persons.value.map((p) => ({ label: p.name, value: p.id })),
)

// ── 文件夹选择（仅文件夹，禁用多文件选择）──
const folderInput = ref<HTMLInputElement | null>(null)
const setName = ref('')

interface PendingPhoto {
  id: string
  file: File
  /** canvas 压缩缩略图 URL（进入视口时异步生成，不直接引用原图） */
  thumbUrl?: string
  /** 缩略图生成中标记 */
  thumbLoading?: boolean
  status: 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'
  progress: number
  errorMsg?: string
}

const pending = ref<PendingPhoto[]>([])

/** 可上传的图片扩展名（模特写真仅图片，不含视频） */
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif'])

// ── 分批渲染 + 缩略图懒加载（避免数百张照片一次性渲染/解码导致页面卡死）──
/** 每批渲染张数 */
const PAGE_SIZE = 50
/** 当前渲染张数（滚动加载/「加载更多」递增） */
const visibleCount = ref(PAGE_SIZE)
/** 当前渲染的照片（分页切片） */
const shownPhotos = computed(() => pending.value.slice(0, visibleCount.value))

function loadMore() {
  visibleCount.value = Math.min(pending.value.length, visibleCount.value + PAGE_SIZE)
}

/** 缩略图生成（canvas 压缩至最长边 320px；createImageBitmap 异步解码不阻塞主线程） */
async function createThumbnailUrl(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, 320 / Math.max(bitmap.width, bitmap.height))
    const w = Math.max(1, Math.round(bitmap.width * scale))
    const h = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('canvas 不可用')
    ctx.drawImage(bitmap, 0, 0, w, h)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.8),
    )
    if (!blob) throw new Error('缩略图生成失败')
    return URL.createObjectURL(blob)
  } finally {
    bitmap.close() // 释放位图内存
  }
}

/** 为指定照片生成缩略图（幂等：已有/生成中则跳过；失败回退原图 objectURL） */
async function ensureThumbnail(id: string) {
  const item = pending.value.find((p) => p.id === id)
  if (!item || item.thumbUrl || item.thumbLoading) return
  item.thumbLoading = true
  try {
    item.thumbUrl = await createThumbnailUrl(item.file)
  } catch {
    // createImageBitmap/canvas 不可用（旧浏览器等）：回退原图 objectURL，保证预览可用
    try {
      item.thumbUrl = URL.createObjectURL(item.file)
    } catch {
      // 仍失败则保持占位
    }
  } finally {
    item.thumbLoading = false
  }
}

/** 缩略图懒加载观察器：img 进入视口（提前 200px）才生成缩略图 */
let thumbObserver: IntersectionObserver | null = null

function onImgMounted(el: unknown) {
  if (el) thumbObserver?.observe(el as HTMLElement)
}

/** 底部哨兵：滚动接近底部时自动加载下一批 */
let sentinelObserver: IntersectionObserver | null = null

function onSentinelMounted(el: unknown) {
  sentinelObserver?.disconnect()
  if (el) {
    sentinelObserver = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadMore()
      },
      { rootMargin: '400px' },
    )
    sentinelObserver.observe(el as HTMLElement)
  }
}

function openFolder() {
  // 上传中禁止更换文件夹，避免上传循环与列表重建错乱
  if (uploading.value) {
    Message.info('导入中，暂不能更换文件夹')
    return
  }
  folderInput.value?.click()
}

function onFolderChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  // 文件夹名取 webkitRelativePath 的第一段（所选文件夹本身）
  const firstRelative = files[0]?.webkitRelativePath || ''
  const folderName = firstRelative.split('/')[0] || ''

  const imageFiles = files.filter((f) => {
    const ext = '.' + (f.name.split('.').pop()?.toLowerCase() || '')
    return IMAGE_EXTS.has(ext)
  })
  if (imageFiles.length === 0) {
    Message.warning('该文件夹中没有可识别的图片文件')
    input.value = ''
    return
  }

  // 按文件名自然排序（模特写真常以 001/002 命名），保持组内顺序稳定
  const sorted = imageFiles.sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true }),
  )

  clearPending()
  // 只登记文件元数据，不立即创建 objectURL；缩略图由懒加载按需生成
  for (const file of sorted) {
    pending.value.push({
      id: crypto.randomUUID(),
      file,
      status: 'pending',
      progress: 0,
    })
  }
  visibleCount.value = PAGE_SIZE
  thumbObserver?.disconnect()
  thumbObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const el = entry.target as HTMLElement
          const id = el.dataset.thumbId
          if (id) void ensureThumbnail(id)
          thumbObserver?.unobserve(el)
        }
      }
    },
    { rootMargin: '200px' },
  )
  if (folderName && !setName.value.trim()) {
    setName.value = folderName
  }
  input.value = ''
}

function clearPending() {
  pending.value.forEach((p) => {
    if (p.thumbUrl) URL.revokeObjectURL(p.thumbUrl)
  })
  pending.value = []
  visibleCount.value = PAGE_SIZE
  thumbObserver?.disconnect()
  sentinelObserver?.disconnect()
}

// ── 上传 ──
const uploading = ref(false)
const uploadedSetId = ref<number | null>(null)

const stats = computed(() => {
  const done = pending.value.filter((p) => p.status === 'done').length
  const failed = pending.value.filter((p) => p.status === 'failed').length
  const dups = pending.value.filter((p) => p.status === 'duplicate').length
  return { done, failed, dups, total: pending.value.length }
})

async function startUpload() {
  // 兜底：从 URL 恢复预选人物（详情页「添加照片」入口 / 页面刷新后状态重建场景）
  if (!personId.value) {
    const q = Number(route.query.person_id)
    if (Number.isInteger(q) && q > 0) personId.value = q
  }
  if (!personId.value) {
    Message.warning('请先选择人物')
    return
  }
  if (pending.value.length === 0) {
    Message.warning('请先选择一个文件夹')
    return
  }
  uploading.value = true
  uploadedSetId.value = null
  try {
    const set = await createModelPhotoSet(personId.value, setName.value.trim() || undefined)
    uploadedSetId.value = set.id

    // 上传用快照：上传期间界面已禁止更换文件夹/人物，双保险避免循环引用被替换
    const items = [...pending.value]
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      item.status = 'uploading'
      item.progress = 0
      try {
        await uploadModelPhoto(personId.value, set.id, item.file, i, (e: any) => {
          if (e?.total > 0) {
            item.progress = Math.min(100, Math.round((e.loaded / e.total) * 100))
          }
        })
        item.status = 'done'
        item.progress = 100
      } catch (err) {
        if ((err as { response?: { status?: number } })?.response?.status === 409) {
          item.status = 'duplicate'
          item.errorMsg = '内容重复已跳过'
        } else {
          item.status = 'failed'
          item.errorMsg = getApiErrorMessage(err, '上传失败')
        }
      }
    }

    const { done, failed, dups } = stats.value
    const parts = [`${done} 成功`]
    if (failed > 0) parts.push(`${failed} 失败`)
    if (dups > 0) parts.push(`${dups} 重复跳过`)
    Message.success(`照片组「${set.name}」导入完成：${parts.join('，')}`)
  } catch (err) {
    Message.error(getApiErrorMessage(err, '创建照片组失败'))
  } finally {
    uploading.value = false
  }
}

/** 跳转到人物详情查看刚导入的照片组 */
function goPersonDetail() {
  if (personId.value) router.push(`/persons/${personId.value}`)
}

// ── 生命周期 ──
onMounted(() => {
  loadPersons()
  // 从人物详情「添加照片」入口带入 person_id 时预选
  const q = Number(route.query.person_id)
  if (Number.isInteger(q) && q > 0) personId.value = q
})

onUnmounted(() => {
  clearPending()
})
</script>

<template>
  <div class="model-photo-page">
    <h2>添加模特照片</h2>
    <a-typography-text type="secondary" style="font-size: 13px">
      选择一个文件夹，把其中所有图片作为一组模特写真导入到某个人物名下
    </a-typography-text>

    <!-- 人物选择 -->
    <a-card size="small" class="step-card" title="第一步 · 选择人物">
      <a-space align="center">
        <a-select
          v-model:value="personId"
          :options="personOptions"
          :loading="personsLoading"
          :disabled="uploading"
          filterable
          clearable
          placeholder="选择模特 / 博主"
          style="width: 280px"
        />
        <a-button type="secondary" @click="showForm = true">＋ 新建人物</a-button>
      </a-space>
    </a-card>

    <!-- 文件夹选择 -->
    <a-card size="small" class="step-card" title="第二步 · 选择文件夹">
      <div class="folder-zone" :class="{ 'folder-zone-disabled': uploading }" @click="openFolder">
        <div class="folder-icon">📁</div>
        <p class="folder-title">{{ uploading ? '导入中，暂不能更换文件夹' : '点击选择文件夹' }}</p>
        <p class="folder-desc">仅支持选择文件夹，会把文件夹内全部图片导入为一组</p>
      </div>
      <input
        ref="folderInput"
        type="file"
        webkitdirectory
        multiple
        accept="image/*"
        style="display: none"
        @change="onFolderChange"
      />

      <a-form-item label="照片组名称" style="margin-top: 16px; max-width: 420px">
        <a-input
          v-model="setName"
          :disabled="uploading"
          placeholder="默认取文件夹名，可修改"
          :max-length="128"
        />
      </a-form-item>
    </a-card>

    <!-- 预览 -->
    <a-card v-if="pending.length > 0" size="small" class="step-card">
      <template #header>
        <a-space align="center" style="display: flex; justify-content: space-between">
          <span>已选 {{ pending.length }} 张照片</span>
          <a-space>
            <a-button size="small" type="text" :disabled="uploading" @click="clearPending">
              清空
            </a-button>
          </a-space>
        </a-space>
      </template>

      <div class="preview-grid">
        <div v-for="(p, i) in shownPhotos" :key="p.id" class="preview-item" :class="p.status">
          <img
            v-if="p.thumbUrl"
            :ref="onImgMounted"
            :data-thumb-id="p.id"
            :src="p.thumbUrl"
            :alt="p.file.name"
          />
          <div v-else class="thumb-placeholder">
            <a-spin v-if="p.thumbLoading" :size="14" />
          </div>
          <div class="preview-index">{{ i + 1 }}</div>
          <div v-if="p.status === 'uploading'" class="preview-mask">
            <a-progress type="circle" :percent="p.progress / 100" :width="44" />
          </div>
          <div v-else-if="p.status === 'done'" class="preview-mask done">✓</div>
          <div v-else-if="p.status === 'failed'" class="preview-mask failed" :title="p.errorMsg">
            ✕
          </div>
          <div v-else-if="p.status === 'duplicate'" class="preview-mask dup" :title="p.errorMsg">
            ⧉
          </div>
        </div>
      </div>

      <!-- 分批加载：滚动到接近底部自动加载下一批；「加载更多」按钮兜底 -->
      <div v-if="visibleCount < pending.length" :ref="onSentinelMounted" class="load-more-row">
        <a-button size="small" @click="loadMore">
          加载更多（已显示 {{ shownPhotos.length }} / {{ pending.length }}）
        </a-button>
      </div>

      <div class="upload-actions">
        <a-button type="primary" size="large" :loading="uploading" @click="startUpload">
          {{ uploading ? '导入中…' : '开始导入' }}
        </a-button>
        <a-typography-text v-if="uploading" type="secondary">
          已完成 {{ stats.done }} / {{ stats.total }}
        </a-typography-text>
      </div>
    </a-card>

    <!-- 完成后跳转 -->
    <a-card v-if="uploadedSetId && !uploading && stats.done > 0" size="small" class="step-card">
      <a-space align="center" style="display: flex; justify-content: space-between">
        <a-typography-text>照片组已导入完成，可前往人物详情查看。</a-typography-text>
        <a-button type="secondary" @click="goPersonDetail">查看人物照片组 →</a-button>
      </a-space>
    </a-card>

    <!-- 新建人物对话框（模特照片页仅创建模特） -->
    <PersonFormModal v-model:show="showForm" kind="model" :person="null" @saved="onPersonCreated" />
  </div>
</template>

<style scoped>
.model-photo-page {
  max-width: 860px;
  margin: 0 auto;
  padding-bottom: 40px;
}

.step-card {
  margin-top: 16px;
}

/* 文件夹选择区域 */
.folder-zone {
  padding: 36px 24px;
  border: 2px dashed #d1d5db;
  border-radius: 12px;
  text-align: center;
  cursor: pointer;
  transition:
    border-color 0.2s,
    background 0.2s;
}

.folder-zone:hover {
  border-color: #818cf8;
  background: #fafafe;
}

/* 上传中禁用文件夹选择 */
.folder-zone-disabled {
  opacity: 0.6;
  pointer-events: none;
}

.folder-icon {
  font-size: 48px;
}

.folder-title {
  margin: 8px 0 4px;
  font-size: 16px;
  font-weight: 600;
  color: #374151;
}

.folder-desc {
  margin: 0;
  font-size: 13px;
  color: #9ca3af;
}

/* 预览网格 */
.preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 10px;
}

.preview-item {
  position: relative;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  overflow: hidden;
  background: #eef1f6;
}

.preview-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 缩略图未生成时的占位 */
.thumb-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eef1f6;
}

/* 分批加载行（滚动监听哨兵 + 加载更多按钮） */
.load-more-row {
  display: flex;
  justify-content: center;
  padding: 14px 0 4px;
}

.preview-index {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}

.preview-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.55);
}

.preview-mask.done {
  background: rgba(16, 185, 129, 0.35);
  color: #065f46;
  font-size: 28px;
  font-weight: 700;
}

.preview-mask.failed {
  background: rgba(239, 68, 68, 0.35);
  color: #7f1d1d;
  font-size: 28px;
  font-weight: 700;
}

.preview-mask.dup {
  background: rgba(250, 204, 21, 0.4);
  color: #713f12;
  font-size: 28px;
  font-weight: 700;
}

.upload-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
}
</style>
