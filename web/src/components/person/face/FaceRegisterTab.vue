<script setup lang="ts">
/** 批量注册 tab：未注册博主的候选脸簇 → 一键注册（阶段 1 工作台）。
 *
 * 数据由 `POST /api/face-scan/register-plan` 在服务端聚好（只读、不写库）；
 * 「采用」逐位调用 `POST /api/face-scan/register-batch`；「采用当前页（20 位）」
 * 按后端约定一次最多 20 位，前端分批循环，不新增任务类型。
 */

import { Message } from '@arco-design/web-vue'
import { computed, onMounted, reactive, ref } from 'vue'

import {
  fetchFaceRegisterPlan,
  registerFaceBatch,
  type FaceRegisterCluster,
  type FaceRegisterPlanItem,
} from '@/api/faceScan'
import { getFileUrl } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  /** 人脸子服务离线：禁用注册动作并给出提示 */
  offline?: boolean
}>()

const emit = defineEmits<{
  (e: 'registered'): void
}>()

const loading = ref(false)
const plan = ref<FaceRegisterPlanItem[]>([])
const summary = ref<{
  total: number
  with_candidates: number
  no_candidates: number
} | null>(null)
const clusterIndex = reactive<Record<number, number>>({})
const skipped = ref<Set<number>>(new Set())
const registered = ref<Set<number>>(new Set())
const busyIds = ref<Set<number>>(new Set())
const batchBusy = ref(false)
const lastErrors = ref<string[]>([])

const visibleItems = computed(() => plan.value.filter((item) => !skipped.value.has(item.person_id)))
const pendingItems = computed(() =>
  visibleItems.value.filter((item) => !registered.value.has(item.person_id)),
)

async function load() {
  loading.value = true
  try {
    const data = await fetchFaceRegisterPlan({ limit: 500 })
    plan.value = data.items
    summary.value = {
      total: data.summary.total,
      with_candidates: data.summary.with_candidates,
      no_candidates: data.summary.no_candidates,
    }
    for (const item of plan.value) clusterIndex[item.person_id] = 0
  } catch (e) {
    Message.error(getApiErrorMessage(e, '读取批量注册计划失败'))
  } finally {
    loading.value = false
  }
}

function currentCluster(item: FaceRegisterPlanItem): FaceRegisterCluster | null {
  const idx = clusterIndex[item.person_id] ?? 0
  return item.clusters[idx] ?? null
}

function nextCluster(item: FaceRegisterPlanItem) {
  if (item.clusters.length < 2) return
  clusterIndex[item.person_id] = ((clusterIndex[item.person_id] ?? 0) + 1) % item.clusters.length
}

function skip(item: FaceRegisterPlanItem) {
  skipped.value.add(item.person_id)
  skipped.value = new Set(skipped.value)
}

function avatarUrl(item: FaceRegisterPlanItem): string | undefined {
  return item.avatar_path ? getFileUrl(item.avatar_path) : undefined
}

function thumbUrl(path: string | null): string {
  return path ? getFileUrl(path) : ''
}

/** 逐位注册（单条）；成功后从待办里移除并通知父组件刷新 */
async function adopt(item: FaceRegisterPlanItem) {
  const cluster = currentCluster(item)
  if (!cluster || cluster.suggested_inspiration_ids.length === 0 || props.offline) return
  busyIds.value.add(item.person_id)
  busyIds.value = new Set(busyIds.value)
  try {
    const result = await registerFaceBatch([
      {
        person_type: 'blogger',
        person_id: item.person_id,
        inspiration_ids: cluster.suggested_inspiration_ids.slice(0, 3),
      },
    ])
    const row = result.items[0]
    if (row?.ok) {
      registered.value.add(item.person_id)
      registered.value = new Set(registered.value)
      Message.success(`已注册「${item.name}」（使用 ${row.face_count} 张素材）`)
      emit('registered')
    } else {
      Message.error(row?.error || '注册失败')
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '注册失败'))
  } finally {
    busyIds.value.delete(item.person_id)
    busyIds.value = new Set(busyIds.value)
  }
}

/** 采用当前页（最多 20 位，一次请求；失败项保留可重试） */
async function adoptPage() {
  if (props.offline) return
  const batch = pendingItems.value.slice(0, 20)
  if (batch.length === 0) {
    Message.info('当前没有可采用的候选')
    return
  }
  batchBusy.value = true
  lastErrors.value = []
  try {
    const result = await registerFaceBatch(
      batch.map((item) => {
        const cluster = currentCluster(item)
        return {
          person_type: 'blogger' as const,
          person_id: item.person_id,
          inspiration_ids: (cluster?.suggested_inspiration_ids ?? []).slice(0, 3),
        }
      }),
    )
    for (const row of result.items) {
      if (row.ok) registered.value.add(row.person_id)
      else lastErrors.value.push(`博主 #${row.person_id}：${row.error || '注册失败'}`)
    }
    registered.value = new Set(registered.value)
    Message.success(`本批采用 ${result.succeeded} 位，失败 ${result.failed} 位`)
    if (result.succeeded > 0) emit('registered')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '批量注册失败'))
  } finally {
    batchBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <a-spin :loading="loading" style="display: block">
    <a-alert v-if="offline" type="warning" style="margin-bottom: 10px">
      人脸识别子服务离线：注册动作已禁用，请先启动 face-service 后点上方「重新检测」。
    </a-alert>
    <div v-if="summary" class="register-summary">
      <span>
        未注册博主 {{ summary.total }} 位；有稳定人脸候选
        <strong>{{ summary.with_candidates }}</strong> 位；无候选 {{ summary.no_candidates }} 位。
        按「簇大小 × 平均置信度」排序，建议从前往后人工确认。
      </span>
      <a-space>
        <a-button
          type="primary"
          size="small"
          :loading="batchBusy"
          :disabled="offline || pendingItems.length === 0"
          @click="adoptPage"
        >
          采用当前页（{{ Math.min(20, pendingItems.length) }} 位）
        </a-button>
        <a-button size="small" :loading="loading" @click="load">重新拉取计划</a-button>
      </a-space>
    </div>
    <a-alert v-if="lastErrors.length" type="error" style="margin-bottom: 10px">
      <template #title>本批失败项（可重试）</template>
      <div v-for="(err, idx) in lastErrors" :key="idx">{{ err }}</div>
    </a-alert>

    <div v-if="visibleItems.length > 0" class="register-list">
      <div v-for="item in visibleItems" :key="item.person_id" class="register-row">
        <div class="row-head">
          <a-avatar :size="36" :image-url="avatarUrl(item)">
            <template v-if="!avatarUrl(item)">{{ item.name.slice(0, 1) }}</template>
          </a-avatar>
          <a
            class="row-name"
            :href="`/persons/${item.person_id}?kind=blogger`"
            target="_blank"
            rel="noopener noreferrer"
            >{{ item.name }}</a
          >
          <a-tag size="small" color="arcoblue">穿搭博主</a-tag>
          <a-typography-text type="secondary" style="font-size: 12px">
            素材 {{ item.inspiration_count }} 张
          </a-typography-text>
          <a-tag v-if="registered.has(item.person_id)" size="small" color="green">已注册</a-tag>
        </div>

        <template v-if="currentCluster(item)">
          <div class="cluster-line">
            候选簇：出现 {{ currentCluster(item)!.size }} 次 · 平均置信度
            {{ currentCluster(item)!.avg_det_score.toFixed(2) }}
            <span v-if="item.clusters.length > 1">
              （第 {{ (clusterIndex[item.person_id] ?? 0) + 1 }} / {{ item.clusters.length }} 组）
            </span>
          </div>
          <div class="cluster-thumbs">
            <img
              v-for="cand in currentCluster(item)!.candidates.slice(0, 3)"
              :key="cand.detection_id ?? cand.inspiration_id"
              :src="thumbUrl(cand.thumbnail_path)"
              class="cluster-thumb"
              loading="lazy"
              :title="`置信度 ${cand.det_score.toFixed(2)}`"
            />
          </div>
          <a-space :size="6">
            <a-button
              type="primary"
              size="mini"
              :loading="busyIds.has(item.person_id)"
              :disabled="offline || registered.has(item.person_id)"
              @click="adopt(item)"
            >
              采用
            </a-button>
            <a-button size="mini" :disabled="item.clusters.length < 2" @click="nextCluster(item)">
              换一组
            </a-button>
            <a-button size="mini" @click="skip(item)">跳过</a-button>
          </a-space>
        </template>
        <a-empty v-else description="素材里没有稳定人脸" size="small" />
      </div>
    </div>
    <a-empty v-else description="暂无有稳定人脸的未注册博主（或全部已处理）" />
  </a-spin>
</template>

<style scoped>
.register-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--color-text-2);
}
.register-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.register-row {
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-name {
  font-weight: 600;
}
.cluster-line {
  font-size: 12px;
  color: var(--color-text-2);
}
.cluster-thumbs {
  display: flex;
  gap: 8px;
}
.cluster-thumb {
  width: 72px;
  height: 72px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid var(--color-border-2);
}
</style>
