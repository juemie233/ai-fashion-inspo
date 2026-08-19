<script setup lang="ts">
/** 素材人脸识别区块：触发检测与博主特征库匹配，展示结果，支持手动指定/解除博主。
 *
 * 逻辑：检测结果（inspiration_face_detections）→ 匹配到的博主 / 疑似未知人脸
 * （低于阈值未匹配）→ 用户可手动选择博主或解除错误关联。
 */

import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  deleteFaceDetection,
  faceDetectInspiration,
  fetchFaceDetections,
  updateFaceDetection,
  type FaceDetectionOut,
  type FaceDetectionsOut,
} from '@/api/inspirations'
import { bloggersApi, type PersonBrief } from '@/api/persons'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  /** 素材 ID */
  inspirationId: string
}>()

const message = useMessage()

const detections = ref<FaceDetectionOut[]>([])
const loading = ref(false)
const detecting = ref(false)
/** 博主候选（手动指定用） */
const bloggerOptions = ref<{ label: string; value: number }[]>([])
/** 正在手动操作的检测 id */
const updatingId = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const data: FaceDetectionsOut = await fetchFaceDetections(props.inspirationId)
    detections.value = data.detections ?? []
  } catch {
    // 加载失败静默（无检测记录时展示空态引导）
  } finally {
    loading.value = false
  }
}

async function loadBloggerOptions() {
  try {
    const { items } = await bloggersApi.fetchList({ sort: 'count', size: 100 })
    bloggerOptions.value = (items as PersonBrief[]).map((b) => ({
      label: b.name,
      value: b.id,
    }))
  } catch {
    // 博主列表加载失败静默：手动选择下拉为空
  }
}

/** 触发检测并匹配（重新检测覆盖旧结果） */
async function handleDetect() {
  detecting.value = true
  try {
    const data = await faceDetectInspiration(props.inspirationId)
    detections.value = data.detections ?? []
    if (data.face_count === 0) {
      message.info('未检测到人脸')
    } else {
      message.success(`检测到 ${data.face_count} 张人脸，已与博主特征库匹配`)
    }
  } catch (e) {
    message.error(getApiErrorMessage(e, '人脸检测失败（请确认人脸识别服务已启动）'))
  } finally {
    detecting.value = false
  }
}

/** 手动指定博主（v-model 变更触发） */
async function handleSelectBlogger(det: FaceDetectionOut, bloggerId: number | null) {
  if (bloggerId === null) return // n-select clear 时不处理
  updatingId.value = det.id
  try {
    await updateFaceDetection(props.inspirationId, det.id, bloggerId)
    message.success('已关联博主')
    await load()
  } catch (e) {
    message.error(getApiErrorMessage(e, '关联失败'))
  } finally {
    updatingId.value = null
  }
}

/** 解除错误关联 */
async function handleUnlink(det: FaceDetectionOut) {
  updatingId.value = det.id
  try {
    await updateFaceDetection(props.inspirationId, det.id, null)
    message.success('已解除关联')
    await load()
  } catch (e) {
    message.error(getApiErrorMessage(e, '解除失败'))
  } finally {
    updatingId.value = null
  }
}

/** 删除单条检测记录 */
async function handleDelete(det: FaceDetectionOut) {
  updatingId.value = det.id
  try {
    await deleteFaceDetection(props.inspirationId, det.id)
    message.success('已删除该人脸检测')
    await load()
  } catch (e) {
    message.error(getApiErrorMessage(e, '删除失败'))
  } finally {
    updatingId.value = null
  }
}

onMounted(() => {
  load()
  loadBloggerOptions()
})
</script>

<template>
  <div class="face-detection-section">
    <div class="face-header">
      <h3 style="margin: 0">人脸识别（博主特征库匹配）</h3>
      <a-button size="mini" type="primary" :loading="detecting" @click="handleDetect">
        检测并匹配
      </a-button>
    </div>

    <div v-if="loading" class="face-tip">加载中…</div>

    <div v-else-if="detections.length === 0" class="face-tip">
      暂无检测结果。点击「检测并匹配」识别图中人脸并关联穿搭博主（需先在博主详情页注册人脸）。
    </div>

    <div v-else class="face-list">
      <div v-for="det in detections" :key="det.id" class="face-item">
        <span class="face-index">人脸 #{{ det.face_index + 1 }}</span>
        <a-tag v-if="det.matched_blogger_id !== null" color="green" size="small">
          {{ det.matched_blogger_name ?? `博主 #${det.matched_blogger_id}` }}
          <template v-if="det.confidence !== null">（{{ (det.confidence * 100).toFixed(1) }}%）</template>
        </a-tag>
        <a-tag v-else color="orange" size="small">疑似未知人脸</a-tag>

        <a-select
          size="small"
          style="width: 160px"
          :options="bloggerOptions"
          :model-value="det.matched_blogger_id ?? undefined"
          :disabled="updatingId === det.id"
          allow-clear
          filterable
          placeholder="手动指定博主"
          @change="(v: unknown) => handleSelectBlogger(det, typeof v === 'number' ? v : null)"
        />
        <a-button
          v-if="det.matched_blogger_id !== null"
          size="mini"
          type="text"
          :disabled="updatingId === det.id"
          @click="handleUnlink(det)"
        >
          解除
        </a-button>
        <a-button
          size="mini"
          type="text"
          status="danger"
          :disabled="updatingId === det.id"
          @click="handleDelete(det)"
        >
          删除
        </a-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.face-detection-section {
  padding: 12px 0;
  border-top: 1px solid #f0f0f0;
}
.face-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.face-tip {
  font-size: 12px;
  color: #999;
  padding: 4px 0;
}
.face-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.face-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.face-index {
  font-size: 12px;
  color: #666;
  min-width: 56px;
}
</style>
