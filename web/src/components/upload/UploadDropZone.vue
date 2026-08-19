<script setup lang="ts">
/** 上传主区域：选择文件、导入文件夹、URL 导入。 */

import { ref } from 'vue'

defineProps<{
  /** URL 导入输入框值 */
  urlInput: string
  /** URL 导入请求进行中 */
  urlImporting: boolean
  /** 队列中已有文件（切换主区域紧凑样式） */
  hasQueue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:urlInput', value: string): void
  (e: 'importUrl'): void
  (e: 'filesSelected', files: File[]): void
}>()

// ── 文件选择 ──
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)

function openFilePicker() { fileInput.value?.click() }
function openFolder() { folderInput.value?.click() }

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length > 0) emit('filesSelected', files)
  input.value = ''
}

// ── URL 输入 ──
function onUrlInput(value: string | null) {
  emit('update:urlInput', value ?? '')
}
</script>

<template>
  <div class="upload-zone" :class="{ 'has-queue': hasQueue }">
    <div class="upload-icon-wrap">📤</div>
    <p class="upload-title">上传穿搭素材</p>
    <p class="upload-desc">拖拽文件到此处、Ctrl+V 粘贴、或点击下方按钮</p>
    <p class="upload-formats">JPG / PNG / WebP / GIF / MP4 · 单次最多 500 个</p>

    <div class="upload-actions">
      <a-button type="primary" size="large" @click="openFilePicker">选择文件</a-button>
      <a-button size="large" @click="openFolder">📁 导入文件夹</a-button>
    </div>

    <input ref="fileInput" type="file" multiple accept="image/*,video/mp4" style="display:none" @change="onFileChange" />
    <input ref="folderInput" type="file" webkitdirectory multiple accept="image/*,video/mp4" style="display:none" @change="onFileChange" />

    <!-- URL 导入 -->
    <div class="url-import">
      <a-input
        :model-value="urlInput"
        size="small"
        placeholder="或粘贴图片 URL 导入..."
        allow-clear
        @input="onUrlInput"
        @press-enter="emit('importUrl')"
      />
      <a-button size="small" :loading="urlImporting" @click="emit('importUrl')" :disabled="!urlInput.trim()">
        导入
      </a-button>
    </div>
  </div>
</template>

<style scoped>
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
</style>
