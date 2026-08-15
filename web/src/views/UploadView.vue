<script setup lang="ts">
/** 上传页：拖拽/粘贴/URL/文件夹，预览编辑，元数据，队列管理，去重检测。 */

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useInspirationsStore } from '@/stores/inspirations'
import { addTagsToInspiration } from '@/api/inspirations'
import apiClient from '@/api/client'
import type { UploadQueueItem } from '@/types/upload'
import { useUploadPrefs } from '@/composables/useUploadPrefs'
import { useRecentUploads } from '@/composables/useRecentUploads'
import UploadDropZone from '@/components/upload/UploadDropZone.vue'
import UploadQueue from '@/components/upload/UploadQueue.vue'
import UploadOptionsPanel from '@/components/upload/UploadOptionsPanel.vue'
import RecentUploads from '@/components/upload/RecentUploads.vue'

const router = useRouter()
const message = useMessage()
const store = useInspirationsStore()

// ── 偏好（localStorage 持久化）──
const { autoAnalyze, afterUpload, skipDuplicates, savePrefs } = useUploadPrefs()

// ── 最近上传（sessionStorage 持久化）──
const { recentUploads, prependRecent } = useRecentUploads()

// ── 图片扩展名 ──
const IMG_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.mp4'])

// ── 上传队列上限（页面文案承诺「单次最多 500 个」）──
const MAX_QUEUE_SIZE = 500

// ── 拖拽状态 ──
const isDragging = ref(false)
const dragCount = ref(0)

// ── 预览队列 ──
const queue = ref<UploadQueueItem[]>([])

// ── 上传状态 ──
const uploading = ref(false)
const uploadSpeed = ref('')
let _lastBytes = 0
let _lastTime = 0

// ── 元数据 ──
const sourceAuthor = ref('')
const quickTags = ref('')

// ── URL 导入 ──
const urlInput = ref('')
const urlImporting = ref(false)

// ── 视频预览（模态框）──
const videoModalOpen = ref(false)
const videoModalSrc = ref('')

// ── 去重 ──
let _dedupHashes = new Set<string>()

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
  // 数量校验：队列已有 + 本次拖入超过上限时，按剩余容量截断
  const remaining = MAX_QUEUE_SIZE - queue.value.length
  const accepted = remaining > 0 ? imageFiles.slice(0, remaining) : []
  if (accepted.length < imageFiles.length) {
    if (remaining <= 0) {
      message.warning(`队列已满（最多 ${MAX_QUEUE_SIZE} 个），未添加任何文件`)
    } else {
      message.warning(`队列已接近上限：本次仅保留前 ${accepted.length} 个文件（上限 ${MAX_QUEUE_SIZE} 个）`)
    }
  }
  for (const file of accepted) {
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
  uploadSpeed.value = ''

  const tags = quickTags.value.split(/[,，\s]+/).map(t => t.trim()).filter(Boolean)

  let taggedCount = 0   // 快速标签添加成功数
  let tagFailedCount = 0  // 快速标签添加失败数

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

      const result = await store.upload(formData, makeProgressHandler(item))
      item.status = 'done'
      item.resultId = result.id
      item.progress = 100

      // 自动 AI 分析
      if (autoAnalyze.value) {
        apiClient.post(`/ai/analyze/${result.id}`).catch(() => {})
      }

      // 关联快速标签（自由类目，来源为手动）
      if (tags.length > 0) {
        try {
          await addTagsToInspiration(result.id, tags, 'free', 'manual')
          taggedCount++
        } catch {
          tagFailedCount++
        }
      }

      prependRecent(result.id, result.thumbnail_path ?? null, result.file_path, result.media_type)
      _lastBytes += item.file.size
    } catch (e: any) {
      item.status = 'failed'
      item.errorMsg = e.response?.data?.detail || '上传失败'
    }
  }

  uploading.value = false
  uploadSpeed.value = ''
  const done = queue.value.filter(q => q.status === 'done').length
  const failed = queue.value.filter(q => q.status === 'failed').length
  const dups = queue.value.filter(q => q.status === 'duplicate').length

  const parts = [`${done} 成功`]
  if (failed > 0) parts.push(`${failed} 失败`)
  if (dups > 0) parts.push(`${dups} 已跳过`)
  message.success('上传完成：' + parts.join('，'))

  // 快速标签处理结果提示
  if (tags.length > 0) {
    if (taggedCount > 0) message.success(`已为 ${taggedCount} 个素材添加快速标签`)
    if (tagFailedCount > 0) message.warning(`${tagFailedCount} 个素材快速标签添加失败`)
  }

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

// ── 视频预览 ──
/** 判断文件是否为视频（按 MIME 类型与扩展名） */
function isVideoFile(file: File): boolean {
  return file.type.startsWith('video/') || /\.(mp4|mov|webm|m4v)$/i.test(file.name)
}

/** 点击队列中的视频项，在模态框中播放预览 */
function previewQueueItem(item: UploadQueueItem) {
  if (!isVideoFile(item.file)) return
  videoModalSrc.value = item.thumbnail
  videoModalOpen.value = true
}

/** 生成上传进度回调：更新单项百分比并计算整体速度 */
function makeProgressHandler(item: UploadQueueItem) {
  return (e: any) => {
    if (e?.total > 0) {
      item.progress = Math.min(100, Math.round((e.loaded / e.total) * 100))
    }
    const totalBytes = _lastBytes + (e?.loaded || 0)
    const elapsed = (Date.now() - _lastTime) / 1000
    const mbps = elapsed > 0 ? totalBytes / 1024 / 1024 / elapsed : 0
    uploadSpeed.value = `${item.progress}% · ${mbps.toFixed(1)} MB/s`
  }
}

function goToDetail(id: string) { router.push(`/detail/${id}`) }

// ── 队列统计 ──
const queuePending = computed(() => queue.value.filter(q => q.status === 'pending').length)
const queueDone = computed(() => queue.value.filter(q => q.status === 'done').length)
const queueFailed = computed(() => queue.value.filter(q => q.status === 'failed').length)
const queueDups = computed(() => queue.value.filter(q => q.status === 'duplicate').length)

// ── 快捷键 ──
/** 清空队列确认弹窗是否可见（按钮与 Esc 共用同一确认） */
const showClearConfirm = ref(false)

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    // 与 SearchView.onGlobalKeydown 对齐：焦点在输入框/文本域时放行，避免干扰输入
    const target = e.target as HTMLElement | null
    const tag = target?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
    // 任一弹窗打开时忽略，避免 Esc 关闭弹窗的同时误弹「清空队列」确认框
    if (videoModalOpen.value || showClearConfirm.value) return
    // Esc 不再直接清空队列，改为弹出确认，防止误触一次性清掉整批文件
    if (queue.value.length === 0 || uploading.value) return
    showClearConfirm.value = true
  }
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
    <UploadDropZone
      v-model:url-input="urlInput"
      :url-importing="urlImporting"
      :has-queue="queue.length > 0"
      @import-url="importFromUrl"
      @files-selected="addFiles"
    />

    <!-- 预览队列 -->
    <UploadQueue
      v-if="queue.length > 0"
      :queue="queue"
      :pending="queuePending"
      :done="queueDone"
      :failed="queueFailed"
      :dups="queueDups"
      :uploading="uploading"
      :speed="uploadSpeed"
      @clear="showClearConfirm = true"
      @remove="removeFromQueue"
      @preview="previewQueueItem"
    />

    <!-- 元数据 + 选项 -->
    <UploadOptionsPanel
      v-if="queue.length > 0"
      v-model:source-author="sourceAuthor"
      v-model:quick-tags="quickTags"
      v-model:auto-analyze="autoAnalyze"
      v-model:skip-duplicates="skipDuplicates"
      v-model:after-upload="afterUpload"
      :uploading="uploading"
      :pending="queuePending"
      @save-prefs="savePrefs"
      @start="startUpload"
    />

    <!-- 最近上传 -->
    <RecentUploads
      v-if="recentUploads.length > 0"
      :items="recentUploads"
      @open-detail="goToDetail"
    />

    <!-- 清空队列确认弹窗 -->
    <n-modal
      v-model:show="showClearConfirm"
      preset="dialog"
      title="确认清空待上传队列？"
      positive-text="确认清空"
      negative-text="取消"
      :positive-button-props="{ type: 'error' }"
      @positive-click="clearQueue"
    >
      已选择的文件将全部移除，此操作不可恢复。
    </n-modal>

    <!-- 视频预览弹窗 -->
    <n-modal
      v-model:show="videoModalOpen"
      preset="card"
      title="视频预览"
      style="width: 640px; max-width: 90vw"
    >
      <video
        v-if="videoModalSrc"
        :src="videoModalSrc"
        controls
        autoplay
        playsinline
        style="width: 100%; border-radius: 8px"
      />
    </n-modal>
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
</style>
