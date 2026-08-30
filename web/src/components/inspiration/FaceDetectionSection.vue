<script setup lang="ts">
/** 素材人脸识别区块：触发检测与特征库匹配，展示结果，支持手动指定/解除博主或模特。
 *
 * 逻辑：检测结果（inspiration_face_detections）→ 匹配到的博主/模特 / 疑似未知人脸
 * （低于阈值未匹配）→ 用户可手动选择人物或解除错误关联。
 * 展示规则：批量扫描的 pending 候选不在素材详情页展示（未审核不显示已关联），
 * 仅展示传统/手动结果（match_status 为 NULL）与已审核（confirmed）结果。
 */

import { computed, onMounted, ref, watch } from 'vue'
import {
  deleteFaceDetection,
  faceDetectInspiration,
  fetchFaceDetections,
  updateFaceDetection,
  type FaceDetectionOut,
  type FaceDetectionsOut,
} from '@/api/inspirations'
import { bloggersApi, modelsApi, type PersonBrief } from '@/api/persons'
import { Message } from '@arco-design/web-vue'
import { IconLock } from '@arco-design/web-vue/es/icon'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  /** 素材 ID */
  inspirationId: string
}>()

const emit = defineEmits<{
  /** 是否存在已确认（锁定）人脸：父组件据此锁定「穿搭博主/职业模特」关联栏 */
  (e: 'lock-change', locked: boolean): void
}>()

const detections = ref<FaceDetectionOut[]>([])
const loading = ref(false)
const detecting = ref(false)
/** 手动指定的人物类型（博主/模特切换） */
const assignKind = ref<'blogger' | 'model'>('blogger')
/** 博主候选（手动指定用） */
const bloggerOptions = ref<{ label: string; value: number }[]>([])
/** 模特候选（手动指定用） */
const modelOptions = ref<{ label: string; value: number }[]>([])
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

/**
 * 加载人物候选列表（人脸检测手动指定用）。
 * 约束：仅返回已注册人脸库且未关联素材的人物（确保检测匹配对象都在特征库内）。
 */
async function loadPersonOptions() {
  try {
    const [{ items: bloggers }, { items: models }] = await Promise.all([
      bloggersApi.fetchList({
        sort: 'count',
        size: 100,
        // 仅保留已注册人脸库的人（确保人脸检测只匹配库内人物）
        face_registered_only: true,
      }),
      modelsApi.fetchList({
        sort: 'count',
        size: 100,
        // 同上
        face_registered_only: true,
      }),
    ])
    bloggerOptions.value = (bloggers as PersonBrief[]).map((b) => ({
      label: b.name,
      value: b.id,
    }))
    modelOptions.value = (models as PersonBrief[]).map((m) => ({
      label: m.name,
      value: m.id,
    }))
  } catch {
    // 人物列表加载失败静默：手动选择下拉为空
  }
}

/** 触发检测并匹配（重新检测覆盖旧结果） */
async function handleDetect() {
  detecting.value = true
  try {
    const data = await faceDetectInspiration(props.inspirationId)
    detections.value = data.detections ?? []
    if (data.face_count === 0) {
      Message.info('未检测到人脸')
    } else {
      Message.success(`检测到 ${data.face_count} 张人脸，已与人物特征库匹配`)
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '人脸检测失败（请确认人脸识别服务已启动）'))
  } finally {
    detecting.value = false
  }
}

/** 手动指定人物（v-model 变更触发） */
async function handleSelectPerson(det: FaceDetectionOut, personId: number | null) {
  if (personId === null) return // select clear 时不处理
  updatingId.value = det.id
  try {
    await updateFaceDetection(props.inspirationId, det.id, personId, assignKind.value)
    Message.success(assignKind.value === 'blogger' ? '已关联博主' : '已关联模特')
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '关联失败'))
  } finally {
    updatingId.value = null
  }
}

/** 解除错误关联 */
async function handleUnlink(det: FaceDetectionOut) {
  updatingId.value = det.id
  try {
    await updateFaceDetection(props.inspirationId, det.id, null)
    Message.success('已解除关联')
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '解除失败'))
  } finally {
    updatingId.value = null
  }
}

/** 删除单条检测记录 */
async function handleDelete(det: FaceDetectionOut) {
  updatingId.value = det.id
  try {
    await deleteFaceDetection(props.inspirationId, det.id)
    Message.success('已删除该人脸检测')
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
  } finally {
    updatingId.value = null
  }
}

/** 手动指定选择器过滤（按名称关键字匹配） */
function filterPersonOption(input: string, option: { label?: string }): boolean {
  const kw = input.trim().toLowerCase()
  if (!kw) return true
  return (option.label ?? '').toLowerCase().includes(kw)
}

/** 低置信度阈值：检测置信度低于该值的人脸不自动匹配（与后端 LOW_CONFIDENCE_THRESHOLD 一致） */
const LOW_CONFIDENCE_THRESHOLD = 0.65

/** 高置信度阈值：匹配相似度 ≥ 该值视为高置信度，直接展示「穿搭博主/职业模特」标签 */
const HIGH_CONFIDENCE_THRESHOLD = 0.9

/** 是否低置信度人脸（模糊/侧脸/小脸，未自动匹配） */
function isLowConfidence(det: FaceDetectionOut): boolean {
  return det.det_score !== null && det.det_score < LOW_CONFIDENCE_THRESHOLD
}

/** 是否已确认（锁定）：扫描审核确认后不可修改/删除 */
function isLocked(det: FaceDetectionOut): boolean {
  return det.match_status === 'confirmed'
}

/** 是否存在已确认（锁定）的人脸：重新检测会覆盖未锁定结果，存在已确认人脸时禁用「检测并匹配」 */
const hasConfirmedFace = computed(() => detections.value.some(isLocked))

// 锁定状态变化时上报父组件（详情页据此锁定「穿搭博主/职业模特」关联栏）
watch(hasConfirmedFace, (locked) => emit('lock-change', locked), { immediate: true })

// 路由复用场景（详情页上一张/下一张切换素材）：inspirationId 变化后重新加载
watch(
  () => props.inspirationId,
  () => load(),
)

/** 命中人物标签（穿搭博主/职业模特）：带人物类型前缀与高置信度标记 */
function matchedTag(det: FaceDetectionOut): { text: string; model: boolean; high: boolean } | null {
  const confidence = det.confidence ?? 0
  if (det.matched_blogger_id !== null) {
    return {
      text: `穿搭博主 ${det.matched_blogger_name ?? `#${det.matched_blogger_id}`}`,
      model: false,
      high: confidence >= HIGH_CONFIDENCE_THRESHOLD,
    }
  }
  if (det.matched_model_id !== null) {
    return {
      text: `职业模特 ${det.matched_model_name ?? `#${det.matched_model_id}`}`,
      model: true,
      high: confidence >= HIGH_CONFIDENCE_THRESHOLD,
    }
  }
  return null
}

onMounted(() => {
  load()
  loadPersonOptions()
})
</script>

<template>
  <div class="face-detection-section">
    <div class="face-header">
      <h3 style="margin: 0">人脸识别（博主/模特特征库匹配）</h3>
      <a-button
        size="mini"
        type="primary"
        :loading="detecting"
        :disabled="hasConfirmedFace"
        :title="
          hasConfirmedFace
            ? '已存在已确认（锁定）的人脸，重新检测会覆盖未锁定结果，已禁用'
            : undefined
        "
        @click="handleDetect"
      >
        检测并匹配
      </a-button>
    </div>

    <div v-if="loading" class="face-tip">加载中…</div>

    <div v-else-if="detections.length === 0" class="face-tip">
      暂无检测结果。点击「检测并匹配」识别图中人脸并关联穿搭博主/职业模特（需先在人物详情页注册人脸特征）。
    </div>

    <div v-else class="face-list">
      <div v-for="det in detections" :key="det.id" class="face-item">
        <span class="face-index">人脸 #{{ det.face_index + 1 }}</span>
        <!-- 已确认（锁定）：绿色标签 + 锁图标，操作按钮禁用 -->
        <a-tag v-if="isLocked(det)" color="green" size="small">
          <IconLock /> 已确认：{{ matchedTag(det)?.text ?? '已关联人物' }}
          <template v-if="det.confidence !== null">
            （{{ (det.confidence * 100).toFixed(1) }}%）
          </template>
        </a-tag>
        <!-- 高置信度：直接显示「穿搭博主/职业模特」绿色标签；中低置信度蓝色提示待确认 -->
        <a-tag
          v-else-if="matchedTag(det)"
          :color="matchedTag(det)?.high ? 'green' : 'arcoblue'"
          size="small"
        >
          {{ matchedTag(det)?.text }}
          <template v-if="det.confidence !== null">
            （{{ (det.confidence * 100).toFixed(1) }}%）
          </template>
          <template v-if="matchedTag(det) && !matchedTag(det)!.high">· 待确认</template>
        </a-tag>
        <a-tag v-else-if="isLowConfidence(det)" color="orange" size="small">
          低置信度人脸（{{ (det.det_score ?? 0).toFixed(2) }}），未自动匹配
        </a-tag>
        <a-tag v-else color="orange" size="small">疑似未知人脸</a-tag>

        <a-radio-group
          v-model="assignKind"
          type="button"
          size="mini"
          :disabled="updatingId !== null"
        >
          <a-radio value="blogger">博主</a-radio>
          <a-radio value="model">模特</a-radio>
        </a-radio-group>
        <a-select
          size="small"
          style="width: 160px"
          :options="assignKind === 'blogger' ? bloggerOptions : modelOptions"
          :model-value="
            assignKind === 'blogger'
              ? (det.matched_blogger_id ?? undefined)
              : (det.matched_model_id ?? undefined)
          "
          :disabled="isLocked(det) || updatingId === det.id"
          allow-clear
          allow-search
          :filter-option="filterPersonOption"
          :placeholder="`手动指定${assignKind === 'blogger' ? '博主' : '模特'}`"
          @change="(v: unknown) => handleSelectPerson(det, typeof v === 'number' ? v : null)"
        />
        <a-button
          v-if="det.matched_blogger_id !== null || det.matched_model_id !== null"
          size="mini"
          type="text"
          :disabled="isLocked(det) || updatingId === det.id"
          @click="handleUnlink(det)"
        >
          解除
        </a-button>
        <a-button
          size="mini"
          type="text"
          status="danger"
          :disabled="isLocked(det) || updatingId === det.id"
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
  flex-wrap: wrap;
}
.face-index {
  font-size: 12px;
  color: #666;
  min-width: 56px;
}
</style>
