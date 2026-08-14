<script setup lang="ts">
/** 上传页：拖拽/粘贴/URL/文件夹，预览编辑，元数据，队列管理，去重检测。 */

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { UploadFileInfo } from 'naive-ui'
import { useInspirationsStore } from '@/stores/inspirations'
import { getFileUrl } from '@/api/inspirations'
import apiClient from '@/api/client'

const router = useRouter()
const message = useMessage()
const store = useInspirationsStore()

// ── 图片扩展名 ──
const IMG_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.mp4'])

// ── 拖拽状态 ──
const isDragging = ref(false)
const dragCount = ref(0)

// ── 预览队列 ──
interface QueueItem {
  id: string
  file: File
  thumbnail: string  // object URL
  status: 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'
  progress: number
  resultId?: string
  errorMsg?: string
}
const queue = ref<QueueItem[]>([])

// ── 上传状态 ──
const uploading = ref(false)
const uploadSpeed = ref('')
let _lastBytes = 0
let _lastTime = 0

// ── 元数据 ──
const sourceAuthor = ref('')
const quickTags = ref('')
const autoAnalyze = ref(localStorage.getItem('upload-auto-analyze') !== 'false')

// ── URL 导入 ──
const urlInput = ref('')
const urlImporting = ref(false)

// ── 视频相关 ──
const videoPreviewUrl = ref('')

// ── 去重 ──
const skipDuplicates = ref(true)
let _dedupHashes = new Set<string>()

// ── 偏好 ──
const afterUpload = ref<'stay' | 'detail' | 'home'>(
  (localStorage.getItem('upload-after') as 'stay' | 'detail' | 'home') || 'stay'
)

// ── 最近上传 ──
const recentUploads = ref<Array<{ id: string; thumbnailPath: string | null; filePath: string; mediaType?: string }>>(
  JSON.parse(sessionStorage.getItem('recent-uploads') || '[]')
)

// ── 文件选择 ──
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)

// ── 拖拽处理 ──
function onDragEnter(e: DragEvent) {
  e.preventDefault()
  isDragging.value = true
  dragCount.value = e.dataTransfer?.items.length || 0
}
function onDragOver(e: DragEvent) { e.preventDefault() }
function onDragLeave(e: DragEvent) {
  if ((e.currentTarget as HTMLElement)?.contains(e.relatedTarget as HTMLElement)) return
  isDragging.value = false
}
function onDrop(e: DragEvent) {
  e.preventDefault()
  isDragging.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.length > 0) addFiles(files)
}

// ── 剪贴板粘贴 ──
function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  const files: File[] = []
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) files.push(file)
    }
  }
  if (files.length > 0) addFiles(files)
}

// ── 添加文件到队列 ──
function addFiles(files: File[]) {
  const imageFiles = files.filter(f => {
    const ext = '.' + (f.name.split('.').pop()?.toLowerCase() || '')
    return IMG_EXTS.has(ext)
  })
  if (imageFiles.length === 0) {
    message.warning('没有可识别的图片文件')
    return
  }
  for (const file of imageFiles) {
    const id = crypto.randomUUID()
    queue.value.push({
      id,
      file,
      thumbnail: URL.createObjectURL(file),
      status: 'pending',
      progress: 0,
    })
  }
}

// ── 移除队列项 ──
function removeFromQueue(id: string) {
  const item = queue.value.find(q => q.id === id)
  if (item) URL.revokeObjectURL(item.thumbnail)
  queue.value = queue.value.filter(q => q.id !== id)
}

function clearQueue() {
  queue.value.forEach(q => URL.revokeObjectURL(q.thumbnail))
  queue.value = []
}

// ── 批量元数据 ──
function applyMetaToAll() {
  // 元数据通过表单传递，上传时自动应用
}

// ── 去重检测 ──
async function checkDuplicate(file: File): Promise<boolean> {
  const buffer = await file.arrayBuffer()
  const hashBuf = await crypto.subtle.digest('SHA-256', buffer)
  const hash = Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2, '0')).join('')
  if (_dedupHashes.has(hash)) return true
  _dedupHashes.add(hash)  // 记录已检测哈希，同批队列内直接拦截重复文件
  try {
    const { data } = await apiClient.get('/admin/check-duplicate', { params: { hash } })
    if (data.exists) return true
  } catch { /* 忽略 */ }
  return false
}

// ── 开始上传 ──
async function startUpload() {
  const pending = queue.value.filter(q => q.status === 'pending')
  if (pending.length === 0) {
    message.warning('没有待上传的文件')
    return
  }
  uploading.value = true
  _lastBytes = 0
  _lastTime = Date.now()

  const tags = quickTags.value.split(',').map(t => t.trim()).filter(Boolean)

  for (const item of pending) {
    // 去重检测
    if (skipDuplicates.value) {
      const dup = await checkDuplicate(item.file)
      if (dup) {
        item.status = 'duplicate'
        item.errorMsg = '文件已存在'
        continue
      }
    }

    item.status = 'uploading'
    try {
      const formData = new FormData()
      formData.append('file', item.file)
      formData.append('source_type', 'manual_upload')
      if (sourceAuthor.value.trim()) formData.append('source_author', sourceAuthor.value.trim())

      const result = await store.upload(formData)
      item.status = 'done'
      item.resultId = result.id
      item.progress = 100

      // 自动 AI 分析
      if (autoAnalyze.value) {
        apiClient.post(`/ai/analyze/${result.id}`).catch(() => {})
      }

      // 关联标签
      if (tags.length > 0) {
        // TODO: add tags to material via API
      }

      prependRecent(result.id, result.thumbnail_path ?? null, result.file_path, result.media_type)
      _lastBytes += item.file.size
    } catch (e: any) {
      item.status = 'failed'
      item.errorMsg = e.response?.data?.detail || '上传失败'
    }
  }

  uploading.value = false
  const done = queue.value.filter(q => q.status === 'done').length
  const failed = queue.value.filter(q => q.status === 'failed').length
  const dups = queue.value.filter(q => q.status === 'duplicate').length

  const parts = [`${done} 成功`]
  if (failed > 0) parts.push(`${failed} 失败`)
  if (dups > 0) parts.push(`${dups} 已跳过`)
  message.success('上传完成：' + parts.join('，'))

  // 上传后行为
  if (afterUpload.value === 'home') router.push('/')
  else if (afterUpload.value === 'detail' && done === 1) {
    const uploaded = queue.value.find(q => q.status === 'done')
    if (uploaded?.resultId) router.push(`/detail/${uploaded.resultId}`)
  }
}

// ── URL 导入 ──
async function importFromUrl() {
  const url = urlInput.value.trim()
  if (!url) return
  urlImporting.value = true
  try {
    const tags = quickTags.value.split(',').map(t => t.trim()).filter(Boolean)
    const { data } = await apiClient.post('/inspirations/from-url', {
      url,
      source_author: sourceAuthor.value.trim() || undefined,
      tags,
    })
    message.success('URL 导入成功')
    prependRecent(data.id, data.thumbnail_path, data.file_path, data.media_type)
    urlInput.value = ''
    if (autoAnalyze.value) {
      apiClient.post(`/ai/analyze/${data.id}`).catch(() => {})
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || 'URL 导入失败')
  } finally {
    urlImporting.value = false
  }
}

// ── 文件选择 ──
function openFilePicker() { fileInput.value?.click() }
function openFolder() { folderInput.value?.click() }

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length > 0) addFiles(files)
  input.value = ''
}

// ── 视频预览 ──
function previewVideo(file: File) {
  URL.revokeObjectURL(videoPreviewUrl.value)
  videoPreviewUrl.value = URL.createObjectURL(file)
}

// ── 最近上传 ──
function prependRecent(id: string, thumbnailPath: string | null, filePath: string, mediaType?: string) {
  recentUploads.value = [
    { id, thumbnailPath, filePath, mediaType },
    ...recentUploads.value.filter(r => r.id !== id),
  ].slice(0, 20)
  sessionStorage.setItem('recent-uploads', JSON.stringify(recentUploads.value))
}

function goToDetail(id: string) { router.push(`/detail/${id}`) }

// ── 队列统计 ──
const queuePending = computed(() => queue.value.filter(q => q.status === 'pending').length)
const queueDone = computed(() => queue.value.filter(q => q.status === 'done').length)
const queueFailed = computed(() => queue.value.filter(q => q.status === 'failed').length)
const queueDups = computed(() => queue.value.filter(q => q.status === 'duplicate').length)

// ── 快捷键 ──
function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') clearQueue()
}

// ── 偏好保存 ──
function savePrefs() {
  localStorage.setItem('upload-auto-analyze', String(autoAnalyze.value))
  localStorage.setItem('upload-after', afterUpload.value)
}

// ── 生命周期 ──
onMounted(() => {
  document.addEventListener('paste', onPaste)
  document.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('paste', onPaste)
  document.removeEventListener('keydown', onKeyDown)
  queue.value.forEach(q => URL.revokeObjectURL(q.thumbnail))
})
</script>

<template>
  <div
    class="upload-page"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <h2>上传素材</h2>

    <!-- 拖拽覆盖层 -->
    <div v-if="isDragging" class="drag-overlay">
      <div class="drag-hint">
        <div class="drag-icon">📥</div>
        <p>松开以上传{{ dragCount > 0 ? ` ${dragCount} 个` : '' }}文件</p>
      </div>
    </div>

    <!-- 上传区域 -->
    <div class="upload-zone" :class="{ 'has-queue': queue.length > 0 }">
      <div class="upload-icon-wrap">📤</div>
      <p class="upload-title">上传穿搭素材</p>
      <p class="upload-desc">拖拽文件到此处、Ctrl+V 粘贴、或点击下方按钮</p>
      <p class="upload-formats">JPG / PNG / WebP / GIF / MP4 · 单次最多 500 个</p>

      <div class="upload-actions">
        <n-button type="primary" size="large" @click="openFilePicker">选择文件</n-button>
        <n-button size="large" @click="openFolder">📁 导入文件夹</n-button>
      </div>

      <input ref="fileInput" type="file" multiple accept="image/*,video/mp4" style="display:none" @change="onFileChange" />
      <input ref="folderInput" type="file" webkitdirectory multiple accept="image/*,video/mp4" style="display:none" @change="onFileChange" />

      <!-- URL 导入 -->
      <div class="url-import">
        <n-input
          v-model:value="urlInput"
          size="small"
          placeholder="或粘贴图片 URL 导入..."
          clearable
          @keyup.enter="importFromUrl"
        />
        <n-button size="small" :loading="urlImporting" @click="importFromUrl" :disabled="!urlInput.trim()">
          导入
        </n-button>
      </div>
    </div>

    <!-- 预览队列 -->
    <div v-if="queue.length > 0" class="queue-section">
      <div class="queue-header">
        <span>上传队列 ({{ queue.length }})</span>
        <span style="font-size:12px;color:#999">
          待上传 {{ queuePending }} · 已完成 {{ queueDone }} · 失败 {{ queueFailed }}
          <template v-if="queueDups > 0"> · 跳过 {{ queueDups }}</template>
        </span>
        <n-space>
          <n-button size="tiny" @click="clearQueue" :disabled="uploading">清空队列</n-button>
        </n-space>
      </div>

      <div class="queue-grid">
        <div
          v-for="item in queue"
          :key="item.id"
          class="queue-card"
          :class="item.status"
        >
          <img :src="item.thumbnail" :alt="item.file.name" />
          <div class="queue-card-status">
            <template v-if="item.status === 'pending'">⏳</template>
            <template v-else-if="item.status === 'uploading'">
              <n-spin size="small" />
            </template>
            <template v-else-if="item.status === 'done'">✅</template>
            <template v-else-if="item.status === 'duplicate'">🔄</template>
            <template v-else-if="item.status === 'failed'">❌</template>
          </div>
          <div class="queue-card-name">{{ item.file.name.slice(0, 20) }}</div>
          <div v-if="item.status === 'failed'" class="queue-card-error" :title="item.errorMsg">
            {{ item.errorMsg?.slice(0, 30) }}
          </div>
          <n-button
            v-if="item.status === 'pending'"
            size="tiny"
            type="error"
            @click="removeFromQueue(item.id)"
          >
            ✕
          </n-button>
        </div>
      </div>
    </div>

    <!-- 元数据 + 选项 -->
    <div v-if="queue.length > 0" class="meta-section">
      <n-card size="small" title="上传选项">
        <div class="meta-grid">
          <div class="meta-row">
            <label>来源作者</label>
            <n-input v-model:value="sourceAuthor" size="small" placeholder="如 Instagram @xxx" />
          </div>
          <div class="meta-row">
            <label>快速标签</label>
            <n-input v-model:value="quickTags" size="small" placeholder="逗号分隔，如：春季, JK制服" />
          </div>
          <div class="meta-row">
            <label>自动 AI 分析</label>
            <n-switch v-model:value="autoAnalyze" @update:value="savePrefs" />
          </div>
          <div class="meta-row">
            <label>跳过重复</label>
            <n-switch v-model:value="skipDuplicates" />
          </div>
          <div class="meta-row">
            <label>上传后</label>
            <n-select
              v-model:value="afterUpload"
              :options="[{label:'留在本页',value:'stay'},{label:'查看详情',value:'detail'},{label:'去素材库',value:'home'}]"
              size="tiny"
              style="width:140px"
              @update:value="savePrefs"
            />
          </div>
        </div>

        <n-button
          type="primary"
          block
          :loading="uploading"
          :disabled="queuePending === 0"
          style="margin-top:12px"
          @click="startUpload"
        >
          {{ uploading ? '上传中...' : `开始上传 (${queuePending} 个)` }}
        </n-button>
      </n-card>
    </div>

    <!-- 视频预览 -->
    <div v-if="videoPreviewUrl" class="video-preview">
      <video :src="videoPreviewUrl" controls style="max-width:400px;max-height:300px;border-radius:8px" />
      <n-button size="tiny" @click="videoPreviewUrl = ''">关闭预览</n-button>
    </div>

    <!-- 最近上传 -->
    <div v-if="recentUploads.length > 0" class="recent-section">
      <h3>最近上传 ({{ recentUploads.length }})</h3>
      <div class="recent-grid">
        <div
          v-for="item in recentUploads"
          :key="item.id"
          class="recent-card"
          @click="goToDetail(item.id)"
        >
          <video
            v-if="item.mediaType === 'video' && !item.thumbnailPath"
            :src="getFileUrl(item.filePath)"
            muted
            playsinline
            preload="metadata"
          />
          <img
            v-else-if="item.thumbnailPath || item.filePath"
            :src="getFileUrl(item.thumbnailPath || item.filePath)"
          />
          <div class="recent-card-overlay"><span>查看详情</span></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.upload-page {
  max-width: 780px;
  margin: 0 auto;
  padding-bottom: 40px;
}

/* 拖拽覆盖层 */
.drag-overlay {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: rgba(99, 102, 241, 0.15);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.drag-hint {
  text-align: center;
  color: #4338ca;
}

.drag-icon {
  font-size: 80px;
  margin-bottom: 16px;
}

.drag-hint p {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
}

/* 上传主区域 */
.upload-zone {
  padding: 48px 32px 36px;
  background: #fff;
  border: 2px dashed #d1d5db;
  border-radius: 16px;
  text-align: center;
  transition: border-color 0.2s, background 0.2s;
}

.upload-zone:hover {
  border-color: #818cf8;
  background: #fafafe;
}

.upload-zone.has-queue {
  padding: 24px 32px 20px;
}

.upload-icon-wrap {
  font-size: 56px;
  margin-bottom: 12px;
}

.upload-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 600;
  color: #374151;
}

.upload-desc {
  margin: 0 0 4px;
  font-size: 14px;
  color: #9ca3af;
}

.upload-formats {
  margin: 0 0 20px;
  font-size: 12px;
  color: #c4c4c4;
}

.upload-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

/* URL 导入 */
.url-import {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  max-width: 500px;
  margin-left: auto;
  margin-right: auto;
}

/* 队列 */
.queue-section {
  margin-top: 16px;
  background: #fff;
  border-radius: 10px;
  padding: 12px;
}

.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
}

.queue-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.queue-card {
  position: relative;
  aspect-ratio: 3/4;
  border-radius: 8px;
  overflow: hidden;
  background: #f5f5f5;
  border: 2px solid #e5e7eb;
}

.queue-card.done { border-color: #22c55e; }
.queue-card.failed { border-color: #ef4444; }
.queue-card.duplicate { border-color: #f59e0b; }

.queue-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.queue-card-status {
  position: absolute;
  top: 4px;
  right: 4px;
  font-size: 16px;
}

.queue-card-name {
  position: absolute;
  bottom: 24px;
  left: 0;
  right: 0;
  font-size: 10px;
  color: #fff;
  background: rgba(0,0,0,0.6);
  padding: 2px 4px;
  text-align: center;
}

.queue-card-error {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  font-size: 10px;
  color: #fff;
  background: rgba(239,68,68,0.8);
  padding: 2px 4px;
  text-align: center;
}

/* 元数据 */
.meta-section {
  margin-top: 16px;
}

.meta-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.meta-row label {
  font-size: 12px;
  color: #666;
  width: 80px;
  flex-shrink: 0;
  text-align: right;
}

/* 视频预览 */
.video-preview {
  margin-top: 16px;
  text-align: center;
}

/* 最近上传 */
.recent-section {
  margin-top: 32px;
}

.recent-section h3 {
  margin: 0 0 12px;
  font-size: 16px;
  font-weight: 500;
}

.recent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}

.recent-card {
  position: relative;
  width: 100%;
  padding-bottom: 150%;
  border-radius: 10px;
  overflow: hidden;
  cursor: pointer;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  transition: transform 0.15s, box-shadow 0.15s;
}

.recent-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.recent-card img,
.recent-card video {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.recent-card-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s;
  color: #fff;
  font-size: 14px;
}

.recent-card:hover .recent-card-overlay {
  opacity: 1;
}
</style>
