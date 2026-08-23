<script setup lang="ts">
/** 上传选项：来源作者、快速标签、自动分析、去重、上传后跳转与开始上传按钮。 */

import { computed } from 'vue'
import type { UploadAfterAction } from '@/types/upload'

const props = defineProps<{
  sourceAuthor: string
  quickTags: string
  autoAnalyze: boolean
  skipDuplicates: boolean
  afterUpload: UploadAfterAction
  uploading: boolean
  pending: number
}>()

const emit = defineEmits<{
  (e: 'update:sourceAuthor', value: string): void
  (e: 'update:quickTags', value: string): void
  (e: 'update:autoAnalyze', value: boolean): void
  (e: 'update:skipDuplicates', value: boolean): void
  (e: 'update:afterUpload', value: UploadAfterAction): void
  (e: 'savePrefs'): void
  (e: 'start'): void
  (e: 'stop'): void
}>()

/** 上传后跳转选项 */
const afterUploadOptions: Array<{ label: string; value: UploadAfterAction }> = [
  { label: '留在本页', value: 'stay' },
  { label: '查看详情', value: 'detail' },
  { label: '去素材库', value: 'home' },
  { label: '去标签分析', value: 'models?tab=queue' },
]

// 文本输入：仅回传值，不触发偏好保存
const sourceAuthorModel = computed({
  get: () => props.sourceAuthor,
  set: (value: unknown) => emit('update:sourceAuthor', (value as string) ?? ''),
})
const quickTagsModel = computed({
  get: () => props.quickTags,
  set: (value: unknown) => emit('update:quickTags', (value as string) ?? ''),
})

// 开关/下拉：回传值并触发偏好保存（与原 savePrefs 行为一致）
const autoAnalyzeModel = computed({
  get: () => props.autoAnalyze,
  set: (value: unknown) => {
    emit('update:autoAnalyze', Boolean(value))
    emit('savePrefs')
  },
})
const skipDuplicatesModel = computed({
  get: () => props.skipDuplicates,
  set: (value: unknown) => {
    emit('update:skipDuplicates', Boolean(value))
    emit('savePrefs')
  },
})
const afterUploadModel = computed({
  get: () => props.afterUpload,
  set: (value: unknown) => {
    emit('update:afterUpload', ((value as UploadAfterAction) || 'stay'))
    emit('savePrefs')
  },
})
</script>

<template>
  <a-card size="small" title="上传选项">
    <div class="meta-grid">
      <div class="meta-row">
        <label>来源作者</label>
        <a-input v-model="sourceAuthorModel" size="small" placeholder="如 Instagram @xxx" />
      </div>
      <div class="meta-row">
        <label>快速标签</label>
        <a-input v-model="quickTagsModel" size="small" placeholder="逗号分隔，如：春季, JK制服" />
      </div>
      <div class="meta-row">
        <label>自动 AI 分析</label>
        <a-switch v-model="autoAnalyzeModel" />
      </div>
      <div class="meta-row">
        <label>跳过重复</label>
        <a-switch v-model="skipDuplicatesModel" />
      </div>
      <div class="meta-row">
        <label>上传后</label>
        <a-select
          v-model="afterUploadModel"
          :options="afterUploadOptions"
          size="mini"
          style="width:140px"
        />
      </div>
    </div>

    <a-button
      type="primary"
      long
      :loading="uploading"
      :disabled="pending === 0"
      style="margin-top:12px"
      @click="emit('start')"
    >
      {{ uploading ? '上传中...' : `开始上传 (${pending} 个)` }}
    </a-button>
    <!-- 上传进行中提供停止入口：当前文件中止、剩余文件保持待上传 -->
    <a-button
      v-if="uploading"
      long
      type="text"
      status="warning"
      style="margin-top:8px"
      @click="emit('stop')"
    >
      停止上传
    </a-button>
  </a-card>
</template>

<style scoped>
/* 元数据 */
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
</style>
