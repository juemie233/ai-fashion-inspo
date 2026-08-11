<script setup lang="ts">
/** 上传页：文件上传 + 文件夹批量导入。 */

import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { CloudUploadOutline, FolderOpenOutline } from '@vicons/ionicons5'
import type { UploadFileInfo } from 'naive-ui'
import { useInspirationsStore } from '@/stores/inspirations'
import { getFileUrl } from '@/api/inspirations'

const router = useRouter()
const message = useMessage()
const store = useInspirationsStore()

// ---- 单文件/拖拽上传 ----
const fileList = ref<UploadFileInfo[]>([])
const uploading = ref(false)
const progress = ref({ done: 0, total: 0 })

// ---- 文件夹导入 ----
const folderInput = ref<HTMLInputElement | null>(null)
const folderUploading = ref(false)
const folderProgress = ref({ done: 0, total: 0, current: '' })
const folderResults = ref<{ success: number; fail: number }>({ success: 0, fail: 0 })

// ---- 最近上传（两种方式合并） ----
const recentUploads = ref<Array<{ id: string; thumbnailPath: string | null; filePath: string }>>([])

/** 图片文件扩展名 */
const IMG_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'])

/** 单文件上传 */
async function handleUpload(options: { file: UploadFileInfo; onFinish: () => void }) {
  uploading.value = true
  progress.value.total++

  try {
    const formData = new FormData()
    if (options.file.file) {
      formData.append('file', options.file.file)
    }
    formData.append('source_type', 'manual_upload')

    const result = await store.upload(formData)
    progress.value.done++
    prependRecent(result.id, result.thumbnail_path, result.file_path)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '上传失败')
  } finally {
    options.onFinish()
    uploading.value = false
  }
}

/** 触发文件夹选择 */
function openFolder() {
  folderInput.value?.click()
}

/** 文件夹选择变化：遍历所有图片文件并逐个上传 */
async function onFolderChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length === 0) return

  // 过滤出图片文件
  const imageFiles = files.filter((f) => {
    const ext = '.' + f.name.split('.').pop()?.toLowerCase()
    return IMG_EXTS.has(ext)
  })

  if (imageFiles.length === 0) {
    message.warning('所选文件夹中没有图片文件')
    input.value = ''
    return
  }

  folderUploading.value = true
  folderProgress.value = { done: 0, total: imageFiles.length, current: '' }
  folderResults.value = { success: 0, fail: 0 }

  for (const file of imageFiles) {
    folderProgress.value.current = file.name
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('source_type', 'manual_upload')

      const result = await store.upload(formData)
      folderProgress.value.done++
      folderResults.value.success++
      prependRecent(result.id, result.thumbnail_path, result.file_path)
    } catch {
      folderResults.value.fail++
    }
  }

  folderUploading.value = false
  input.value = ''

  if (folderResults.value.fail > 0) {
    message.warning(
      `导入完成：${folderResults.value.success} 成功，${folderResults.value.fail} 失败`
    )
  } else {
    message.success(`成功导入 ${folderResults.value.success} 张图片`)
  }
}

/** 将上传结果插入最近列表（去重，最多保留 20 条） */
function prependRecent(id: string, thumbnailPath: string | null, filePath: string) {
  recentUploads.value = [
    { id, thumbnailPath, filePath },
    ...recentUploads.value.filter((r) => r.id !== id),
  ].slice(0, 20)
}

function goToDetail(id: string) {
  router.push(`/detail/${id}`)
}
</script>

<template>
  <div class="upload-window">
    <!-- ===== 上传主区域 ===== -->
    <div class="upload-zone">
      <div class="upload-icon-wrap">
        <CloudUploadOutline class="upload-icon-main" />
      </div>
      <p class="upload-title">上传穿搭素材</p>
      <p class="upload-desc">支持 JPG / PNG / WebP / GIF / MP4，单次最多 500 个文件</p>

      <!-- 按钮组 -->
      <div class="upload-actions">
        <n-upload
          v-model:file-list="fileList"
          multiple
          directory-dnd
          accept="image/jpeg,image/png,image/webp,image/gif,video/mp4"
          :max="500"
          :custom-request="handleUpload"
          :disabled="uploading"
          :show-file-list="false"
        >
          <n-button type="primary" size="large" :loading="uploading">
            {{ uploading ? '上传中...' : '选择文件' }}
          </n-button>
        </n-upload>

        <n-button
          size="large"
          :disabled="folderUploading"
          :loading="folderUploading"
          @click="openFolder"
        >
          <template #icon>
            <FolderOpenOutline />
          </template>
          导入文件夹
        </n-button>
      </div>

      <!-- 隐藏的文件夹选择器 -->
      <input
        ref="folderInput"
        type="file"
        webkitdirectory
        multiple
        accept="image/*"
        style="display: none"
        @change="onFolderChange"
      />
    </div>

    <!-- ===== 文件选择提示 ===== -->
    <div v-if="fileList.length > 0" class="hint-bar">
      <n-tag type="info" size="small">
        已选择 {{ fileList.length }} 个文件
      </n-tag>
    </div>

    <!-- ===== 单文件上传进度 ===== -->
    <div v-if="progress.total > 0" class="progress-section">
      <n-progress
        type="line"
        :percentage="Math.round((progress.done / progress.total) * 100)"
        :indicator-placement="'inside'"
        :status="progress.done === progress.total ? 'success' : 'default'"
        :height="20"
      />
      <p class="progress-text">{{ progress.done }} / {{ progress.total }}</p>
    </div>

    <!-- ===== 文件夹导入进度 ===== -->
    <div v-if="folderUploading || folderResults.success > 0" class="folder-progress-section">
      <n-progress
        type="line"
        :percentage="Math.round((folderProgress.done / folderProgress.total) * 100)"
        :indicator-placement="'inside'"
        :status="folderUploading ? 'default' : 'success'"
        :height="22"
        :color="folderUploading ? '#6366f1' : '#16a34a'"
      />
      <p class="folder-current" v-if="folderUploading">
        正在导入：{{ folderProgress.current }}
      </p>
      <p class="folder-progress-text">
        {{ folderUploading ? `${folderProgress.done} / ${folderProgress.total}` : `完成：${folderResults.success} 成功，${folderResults.fail} 失败` }}
      </p>
    </div>

    <!-- ===== 最近上传缩略图 ===== -->
    <div v-if="recentUploads.length > 0" class="recent-section">
      <h3 class="section-title">最近上传 ({{ recentUploads.length }})</h3>
      <div class="recent-grid">
        <div
          v-for="item in recentUploads"
          :key="item.id"
          class="recent-card"
          @click="goToDetail(item.id)"
        >
          <img
            v-if="item.thumbnailPath || item.filePath"
            :src="getFileUrl(item.thumbnailPath || item.filePath)"
          />
          <div class="recent-card-overlay">
            <span>查看详情</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 页面容器 ===== */
.upload-window {
  width: 100%;
  max-width: 680px;
  margin: 0 auto;
  padding-bottom: 40px;
}

/* ===== 上传主区域 ===== */
.upload-zone {
  width: 100%;
  padding: 56px 32px 40px;
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

.upload-icon-wrap {
  margin-bottom: 20px;
}

.upload-icon-main {
  font-size: 64px;
  color: #a5b4fc;
}

.upload-title {
  margin: 0 0 8px 0;
  font-size: 18px;
  font-weight: 600;
  color: #374151;
}

.upload-desc {
  margin: 0 0 28px 0;
  font-size: 14px;
  color: #9ca3af;
  line-height: 1.5;
}

/* 按钮组 */
.upload-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

/* ===== 提示条 ===== */
.hint-bar {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

/* ===== 进度区域 ===== */
.progress-section,
.folder-progress-section {
  margin-top: 20px;
  background: #fff;
  padding: 16px;
  border-radius: 10px;
}

.progress-text {
  margin: 8px 0 0 0;
  font-size: 13px;
  color: #6b7280;
  text-align: center;
}

.folder-current {
  margin: 8px 0 4px 0;
  font-size: 13px;
  color: #6366f1;
  text-align: center;
}

.folder-progress-text {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: #6b7280;
  text-align: center;
}

/* ===== 最近上传 ===== */
.recent-section {
  margin-top: 32px;
}

.section-title {
  margin: 0 0 14px 0;
  font-size: 16px;
  font-weight: 500;
  color: #1f2937;
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

.recent-card img {
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
