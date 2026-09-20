<script setup lang="ts">
/** 人脸库扫描页：全库批量检测人脸 → 矩阵匹配产出候选 → 人工审核确认。
 *
 * 三层流程：
 * 1. 扫描任务（face_scan）：批量检测素材人脸落库（增量/全量），运行中可取消；
 * 2. 候选匹配任务（face_match）：全库矩阵比对产出 pending 候选；
 * 3. 审核确认：待审核区按人物批量确认/驳回（含 ≥0.75 快捷确认），
 *    已确认区可撤销，未匹配区可批量指派人物。
 */

import { Message } from '@arco-design/web-vue'
import { computed, onMounted, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { bloggersApi, modelsApi } from '@/api/persons'
import {
  confirmFaceScan,
  fetchFaceScanResults,
  fetchFaceScanTask,
  runFaceMatch,
  startFaceScan,
  type DetectionItem,
  type FaceScanTaskOut,
  type PersonAggregateItem,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'
import { openInspiration } from '@/utils/openInspiration'
import { pollTaskUntilIdle } from '@/utils/taskPollUntilIdle'
import { useFaceClusterGroups } from '@/composables/useFaceClusterGroups'
import { usePolling } from '@/composables/usePolling'
import StatusTag from '@/components/common/StatusTag.vue'
import FaceClusterTab from '@/components/person/face/FaceClusterTab.vue'
import FaceConfirmedTab from '@/components/person/face/FaceConfirmedTab.vue'
import FacePendingTab from '@/components/person/face/FacePendingTab.vue'
import FaceUnmatchedTab from '@/components/person/face/FaceUnmatchedTab.vue'
// 结果区样式已随模板拆到子组件：scoped 样式不跨组件生效，故收敛到共享样式表
// （统一挂在 .face-scan-page 下，仅本页生效）
import '@/styles/faceScan.css'

// ── 任务区 ──
const scanTask = ref<FaceScanTaskOut | null>(null)
const matchTask = ref<FaceScanTaskOut | null>(null)
// 扫描模式：半增量默认（跳过已确认素材）；全量重扫保留锁定记录
const scope = ref<'incremental' | 'semi' | 'all'>('semi')
// 自动全库匹配默认关闭：扫完是否自动比对特征库由用户显式开启
const autoMatch = ref(false)
const starting = ref(false)
const cancelling = ref(false)
const matching = ref(false)

/** 是否有任务在运行（决定轮询与按钮态）；
 *  聚类任务状态（clusterTask）声明于下方聚类区，computed 惰性求值无时序问题 */
const busy = computed(
  () =>
    scanTask.value?.status === 'running' ||
    scanTask.value?.status === 'pending' ||
    matchTask.value?.status === 'running' ||
    matchTask.value?.status === 'pending' ||
    clusterTask.value?.status === 'running' ||
    clusterTask.value?.status === 'pending',
)

async function refreshTasks() {
  try {
    const status = await fetchFaceScanTask()
    scanTask.value = status.scan_task
    matchTask.value = status.match_task
  } catch (e) {
    Message.error(getApiErrorMessage(e, '获取任务状态失败'))
  }
}

/** 开始扫描（增量/全量） */
async function startScan() {
  starting.value = true
  try {
    const { task_id, total } = await startFaceScan(scope.value, autoMatch.value)
    Message.success(`扫描任务已创建（待扫 ${total} 个素材）`)
    detailChecked.value.clear()
    await refreshTasks()
    void pollUntilIdle(task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建扫描任务失败'))
  } finally {
    starting.value = false
  }
}

/** 取消任务（运行中的人脸任务也可取消，增量续跑） */
async function cancelTask() {
  const task = scanTask.value ?? matchTask.value
  if (!task) return
  cancelling.value = true
  try {
    const { data } = await import('@/api/client').then((m) =>
      m.default.post(`/tasks/${task.id}/cancel`),
    )
    Message.success((data as { message?: string }).message || '任务已取消')
    await refreshTasks()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '取消失败'))
  } finally {
    cancelling.value = false
  }
}

/** 全库重匹配（不动 GPU，秒级~分钟级） */
async function startMatch() {
  matching.value = true
  try {
    const { task_id } = await runFaceMatch({ scope: 'all' })
    Message.success('全库重匹配任务已创建')
    await refreshTasks()
    void pollUntilIdle(task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建匹配任务失败'))
  } finally {
    matching.value = false
  }
}

/** 轮询扫描/匹配任务直到终态（3s 间隔；任务完成后刷新结果区） */
async function pollUntilIdle(taskId: number) {
  await pollTaskUntilIdle({
    taskId,
    intervalMs: 3000,
    refresh: async () => {
      const status = await fetchFaceScanTask()
      scanTask.value = status.scan_task
      matchTask.value = status.match_task
      return [status.scan_task, status.match_task].find((t) => t?.id === taskId) ?? null
    },
    onIdle: refreshAll,
  })
}

// ── 结果区：聚合（按人物）──
/** 结果区 tab：待审核候选 / 已确认 / 未匹配人脸 / 聚合分组 */
type ResultTab = 'pending' | 'confirmed' | 'unmatched' | 'cluster'
const VALID_RESULT_TABS: ResultTab[] = ['pending', 'confirmed', 'unmatched', 'cluster']
// 从 URL query 恢复当前 tab，刷新/分享链接后不再回到第一个页面
const route = useRoute()
const router = useRouter()
const resultTab = ref<ResultTab>(
  VALID_RESULT_TABS.includes(route.query.tab as ResultTab)
    ? (route.query.tab as ResultTab)
    : 'pending',
)

// tab 变更时同步到 URL（默认 tab 不写入 query，保持 URL 干净；与 ModelManageView 等页一致）
watch(resultTab, (tab) => {
  const query = { ...route.query }
  if (tab === 'pending') delete query.tab
  else query.tab = tab
  router.replace({ query })
})
const pendingPersons = ref<PersonAggregateItem[]>([])
const pendingPage = ref(1)
const pendingTotal = ref(0)
const confirmedPersons = ref<PersonAggregateItem[]>([])
const confirmedPage = ref(1)
const confirmedTotal = ref(0)
const personsLoading = ref(false)

async function loadAggregates() {
  personsLoading.value = true
  try {
    const [pending, confirmed] = await Promise.all([
      fetchFaceScanResults({ status: 'pending', page: pendingPage.value, size: 50 }),
      fetchFaceScanResults({ status: 'confirmed', page: confirmedPage.value, size: 50 }),
    ])
    pendingPersons.value = pending.items as PersonAggregateItem[]
    pendingTotal.value = pending.total
    confirmedPersons.value = confirmed.items as PersonAggregateItem[]
    confirmedTotal.value = confirmed.total
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载结果失败'))
  } finally {
    personsLoading.value = false
  }
}

// ── 明细（按人物展开）──
const detailKey = ref('') // `${person_type}:${person_id}`
const detailItems = ref<DetectionItem[]>([])
const detailPage = ref(1)
const detailTotal = ref(0)
const detailLoading = ref(false)
const detailChecked = ref<Set<number>>(new Set())
const detailActionBusy = ref(false)
/** 候选网格列数（3~6 可选，默认 6） */
const gridColumns = ref<number>(6)

const selectedPerson = computed(() => {
  const [type, id] = detailKey.value.split(':')
  return { personType: type as 'blogger' | 'model', personId: Number(id) }
})

/** 展开/收起某人物明细 */
async function toggleDetail(person: PersonAggregateItem) {
  const key = `${person.person_type}:${person.person_id}`
  if (detailKey.value === key) {
    detailKey.value = ''
    detailChecked.value = new Set()
    return
  }
  detailKey.value = key
  detailChecked.value = new Set()
  detailPage.value = 1
  await loadDetail()
}

async function loadDetail() {
  if (!detailKey.value) return
  detailLoading.value = true
  try {
    const { personType, personId } = selectedPerson.value
    const data = await fetchFaceScanResults({
      status: resultTab.value === 'confirmed' ? 'confirmed' : 'pending',
      person_type: personType,
      person_id: personId,
      page: detailPage.value,
      size: 50,
    })
    detailItems.value = data.items as DetectionItem[]
    detailTotal.value = data.total
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载明细失败'))
  } finally {
    detailLoading.value = false
  }
}

/** 批量审核当前人物的候选（confirm/reject），items 为空 = 全部 */
async function actOnPerson(
  person: PersonAggregateItem,
  action: 'confirm' | 'reject',
  onlyHighConfidence = false,
) {
  detailActionBusy.value = true
  try {
    const { person_type: personType, person_id: personId } = person
    const all = await fetchAllDetections(
      resultTab.value === 'confirmed' ? 'confirmed' : 'pending',
      personType,
      personId,
    )
    const items = all
      .filter((d) => !onlyHighConfidence || (d.confidence ?? 0) >= 0.75)
      .map((d) => ({
        detection_id: d.detection_id,
        person_type: action === 'confirm' ? personType : undefined,
        person_id: action === 'confirm' ? personId : undefined,
      }))
    if (items.length === 0) {
      Message.info('没有符合条件的候选')
      return
    }
    const result = await confirmFaceScan(action, items)
    Message.success(
      action === 'confirm'
        ? `已确认关联 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`
        : `已驳回 ${result.rejected} 条`,
    )
    await refreshAll()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '审核操作失败'))
  } finally {
    detailActionBusy.value = false
  }
}

/** 当前人物明细：勾选批量确认/驳回（结果区 tab 决定目标状态） */
async function actOnChecked(action: 'confirm' | 'reject') {
  if (detailChecked.value.size === 0) {
    Message.warning('请先勾选要处理的人脸')
    return
  }
  const { personType, personId } = selectedPerson.value
  detailActionBusy.value = true
  try {
    const result = await confirmFaceScan(
      action,
      [...detailChecked.value].map((id) => ({
        detection_id: id,
        person_type: action === 'confirm' ? personType : undefined,
        person_id: action === 'confirm' ? personId : undefined,
      })),
    )
    Message.success(
      action === 'confirm' ? `已确认关联 ${result.confirmed} 条` : `已驳回 ${result.rejected} 条`,
    )
    detailChecked.value.clear()
    await refreshAll()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '审核操作失败'))
  } finally {
    detailActionBusy.value = false
  }
}

/** 拉取某人物全部明细（分页循环，供批量审核） */
async function fetchAllDetections(
  status: 'pending' | 'confirmed',
  personType: 'blogger' | 'model',
  personId: number,
): Promise<DetectionItem[]> {
  const all: DetectionItem[] = []
  let page = 1
  while (true) {
    const data = await fetchFaceScanResults({
      status,
      person_type: personType,
      person_id: personId,
      page,
      size: 200,
    })
    all.push(...(data.items as DetectionItem[]))
    if (all.length >= data.total) break
    page += 1
  }
  return all
}

// ── 未匹配区 ──
const unmatchedItems = ref<DetectionItem[]>([])
const unmatchedPage = ref(1)
const unmatchedTotal = ref(0)
const unmatchedLoading = ref(false)
const unmatchedChecked = ref<Set<number>>(new Set())
const assignKind = ref<'blogger' | 'model'>('blogger')
const assignPersonId = ref<number | undefined>(undefined)
const assignOptions = ref<Array<{ label: string; value: number }>>([])
const assignLoading = ref(false)
const assigning = ref(false)

async function loadUnmatched() {
  unmatchedLoading.value = true
  try {
    const data = await fetchFaceScanResults({
      status: 'pending',
      unmatched: true,
      page: unmatchedPage.value,
      size: 50,
    })
    unmatchedItems.value = data.items as DetectionItem[]
    unmatchedTotal.value = data.total
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载未匹配人脸失败'))
  } finally {
    unmatchedLoading.value = false
  }
}

/** 拉取人物选择候选（博主/模特全量，排除无意义项） */
async function loadAssignOptions() {
  assignLoading.value = true
  try {
    const api = assignKind.value === 'blogger' ? bloggersApi : modelsApi
    const all: Array<{ id: number; name: string }> = []
    let page = 1
    while (true) {
      const { items, total } = await api.fetchList({ page, size: 200, sort: 'name' })
      all.push(...items)
      if (all.length >= total) break
      page += 1
    }
    assignOptions.value = all.map((p) => ({ label: p.name, value: p.id }))
    assignPersonId.value = undefined
  } catch {
    assignOptions.value = []
  } finally {
    assignLoading.value = false
  }
}

/** 未匹配批量指派给某个人物（写关联 + 置 confirmed） */
async function assignUnmatched() {
  if (unmatchedChecked.value.size === 0) {
    Message.warning('请先勾选要指派的人脸')
    return
  }
  if (!assignPersonId.value) {
    Message.warning('请选择要指派的人物')
    return
  }
  assigning.value = true
  try {
    const result = await confirmFaceScan(
      'confirm',
      [...unmatchedChecked.value].map((id) => ({
        detection_id: id,
        person_type: assignKind.value,
        person_id: assignPersonId.value,
      })),
    )
    Message.success(
      `已指派 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
    )
    unmatchedChecked.value.clear()
    await refreshAll()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '指派失败'))
  } finally {
    assigning.value = false
  }
}

// ── 人脸聚合分组（未匹配人脸按疑似同一人聚类）──
// 状态、请求与轮询全部搬到 useFaceClusterGroups（与扫描/匹配/审核链路解耦）；
// 同名解构，模板无需改动。唯一的跨域依赖「指派后刷新未匹配区」以回调注入。
const {
  clusterTask,
  clusterGroups,
  clusterTotal,
  clusterPage,
  clusterLoading,
  clustering,
  expandedGroupId,
  groupDetailItems,
  groupDetailTotal,
  groupDetailPage,
  groupDetailLoading,
  groupChecked,
  groupActionBusy,
  clusterAssignKind,
  clusterAssignPersonId,
  loadClusterTask,
  startCluster,
  loadClusterGroups,
  toggleGroupDetail,
  loadGroupDetail,
  assignGroup,
  assignCheckedGroup,
  selectAllGroup,
  clearGroupChecked,
} = useFaceClusterGroups({ loadUnmatched: () => loadUnmatched() })
// ── 汇总刷新 ──
async function refreshAll() {
  await Promise.all([
    refreshTasks(),
    loadAggregates(),
    loadUnmatched(),
    loadClusterTask(),
    reloadDetailIfOpen(),
  ])
}

async function reloadDetailIfOpen() {
  if (detailKey.value) {
    detailPage.value = 1
    await loadDetail()
  }
}

// ── 生命周期 ──
// 任务状态轮询：有任务在跑时每 3s 刷新一次。定时器句柄与卸载清理交给 usePolling
// （原先手写的 window.setInterval + onBeforeUnmount(clearInterval) 正是它要替掉的骨架）。
const { start: startTaskPolling } = usePolling({
  intervalMs: 3000,
  immediate: false, // 首帧由下面 onMounted 的 refreshTasks 负责，避免重复请求
  callback: () => {
    if (busy.value) void refreshTasks()
  },
})

onMounted(async () => {
  await refreshTasks()
  await refreshAll()
  await loadClusterGroups()
  await loadAssignOptions()
  startTaskPolling()
})

// ── 结果区分页/事件回调（原为模板内联箭头函数，拆组件后收敛为命名处理函数）──
function onPendingPageChange(p: number) {
  pendingPage.value = p
  loadAggregates()
}

function onConfirmedPageChange(p: number) {
  confirmedPage.value = p
  loadAggregates()
}

function onDetailPageChange(p: number) {
  detailPage.value = p
  loadDetail()
}

function onUnmatchedPageChange(p: number) {
  unmatchedPage.value = p
  loadUnmatched()
}

function onClusterPageChange(p: number) {
  clusterPage.value = p
  loadClusterGroups()
}

function onGroupDetailPageChange(p: number) {
  groupDetailPage.value = p
  loadGroupDetail()
}

/** 素材是否视频：media_type 为 video 时 file_path 是 mp4，不能当 <img> 加载 */
function goDetail(inspirationId: string) {
  openInspiration(router, inspirationId)
}

/** 勾选/取消（Arco checkbox change 值类型较宽，统一按真值处理；替换式 Set 触发响应式） */
function toggleChecked(target: Ref<Set<number>>, id: number, checked: unknown) {
  const next = new Set(target.value)
  if (checked) {
    next.add(id)
  } else {
    next.delete(id)
  }
  target.value = next
}

/** 明细勾选（模板中 ref 自动解包，经 wrapper 传 ref 对象） */
function toggleDetailChecked(id: number, checked: unknown) {
  toggleChecked(detailChecked, id, checked)
}

/** 未匹配勾选（同上） */
function toggleUnmatchedChecked(id: number, checked: unknown) {
  toggleChecked(unmatchedChecked, id, checked)
}

/** 聚合分组组内人脸勾选（同上） */
function toggleGroupChecked(id: number, checked: unknown) {
  toggleChecked(groupChecked, id, checked)
}

/** 全选/取消全选当前明细页（已全部勾选时点击为取消全选） */
function toggleSelectAllDetail() {
  if (detailItems.value.length === 0) return
  const next = new Set(detailChecked.value)
  const allSelected = detailItems.value.every((i) => next.has(i.detection_id))
  if (allSelected) {
    detailItems.value.forEach((i) => next.delete(i.detection_id))
  } else {
    detailItems.value.forEach((i) => next.add(i.detection_id))
  }
  detailChecked.value = next
}

/** 人物选择器过滤（按名称关键字匹配） */
function filterOption(input: string, option: { label?: string }): boolean {
  const kw = input.trim().toLowerCase()
  if (!kw) return true
  return (option.label ?? '').toLowerCase().includes(kw)
}
</script>

<template>
  <div class="face-scan-page">
    <!-- 任务卡片 -->
    <a-card size="small" class="task-card" title="人脸库扫描">
      <template #extra>
        <a-space>
          <a-select
            v-model="scope"
            :options="[
              { label: '半增量扫描（跳过已确认素材）', value: 'semi' },
              { label: '增量扫描（仅未扫描素材）', value: 'incremental' },
              { label: '全量重扫（保留已确认记录）', value: 'all' },
            ]"
            size="small"
            style="width: 240px"
          />
          <a-checkbox v-model="autoMatch" size="small">扫完自动全库匹配</a-checkbox>
          <a-button
            size="small"
            type="primary"
            :loading="starting"
            :disabled="busy"
            @click="startScan"
          >
            开始扫描
          </a-button>
          <a-button size="small" :loading="matching" :disabled="busy" @click="startMatch">
            全库重匹配
          </a-button>
          <a-button
            size="small"
            status="danger"
            :loading="cancelling"
            :disabled="!busy"
            @click="cancelTask"
          >
            取消任务
          </a-button>
        </a-space>
      </template>

      <!-- 扫描任务进度 -->
      <div v-if="scanTask" class="task-line">
        <StatusTag :status="scanTask.status" />
        <a-progress
          v-if="['running', 'pending'].includes(scanTask.status)"
          :percent="scanTask.progress / 100"
          size="small"
          style="width: 320px"
        />
        <a-typography-text type="secondary" style="font-size: 12px">
          扫描素材：{{ scanTask.done }} / {{ scanTask.total }}
          <template v-if="scanTask.result?.scanned !== undefined">
            · 检出人脸 {{ scanTask.result.faces }} 张
            <template v-if="scanTask.result.failed_files">
              · 失败 {{ scanTask.result.failed_files }} 个
            </template>
          </template>
        </a-typography-text>
      </div>
      <a-typography-text v-else type="secondary" style="font-size: 12px">
        尚未运行过扫描。首次全量扫描约 10~20 分钟（GPU），增量扫描秒级，可随时取消后续跑。
      </a-typography-text>

      <!-- 匹配任务进度 -->
      <div v-if="matchTask" class="task-line">
        匹配 <StatusTag :status="matchTask.status" />
        <a-progress
          v-if="['running', 'pending'].includes(matchTask.status)"
          :percent="matchTask.progress / 100"
          size="small"
          style="width: 320px"
        />
        <a-typography-text type="secondary" style="font-size: 12px">
          <template v-if="matchTask.result?.matched !== undefined">
            全库比对 {{ matchTask.result.total_faces }} 张人脸 · 命中
            {{ matchTask.result.matched }} · 未命中 {{ matchTask.result.unmatched }}
          </template>
        </a-typography-text>
      </div>
      <a-typography-text
        v-if="scanTask?.error || matchTask?.error"
        type="danger"
        style="font-size: 12px"
      >
        {{ scanTask?.error || matchTask?.error }}
      </a-typography-text>
    </a-card>

    <!-- 结果区 -->
    <a-card size="small" class="results-card">
      <a-tabs v-model:active-key="resultTab" type="line" @change="refreshAll">
        <!-- 待审核候选 -->
        <a-tab-pane key="pending" title="待审核候选">
          <FacePendingTab
            v-model:grid-columns="gridColumns"
            :persons="pendingPersons"
            :loading="personsLoading"
            :page="pendingPage"
            :total="pendingTotal"
            :detail-key="detailKey"
            :detail-items="detailItems"
            :detail-page="detailPage"
            :detail-total="detailTotal"
            :detail-loading="detailLoading"
            :detail-checked="detailChecked"
            :action-busy="detailActionBusy"
            @toggle-detail="toggleDetail"
            @act-on-person="actOnPerson"
            @page-change="onPendingPageChange"
            @detail-page-change="onDetailPageChange"
            @toggle-detail-checked="toggleDetailChecked"
            @select-all-detail="toggleSelectAllDetail"
            @act-on-checked="actOnChecked"
            @open-inspiration="goDetail"
          />
        </a-tab-pane>

        <!-- 已确认 -->
        <a-tab-pane key="confirmed" title="已确认">
          <FaceConfirmedTab
            :persons="confirmedPersons"
            :loading="personsLoading"
            :page="confirmedPage"
            :total="confirmedTotal"
            :detail-key="detailKey"
            :detail-items="detailItems"
            :detail-page="detailPage"
            :detail-total="detailTotal"
            :detail-loading="detailLoading"
            @toggle-detail="toggleDetail"
            @page-change="onConfirmedPageChange"
            @detail-page-change="onDetailPageChange"
            @open-inspiration="goDetail"
          />
        </a-tab-pane>

        <!-- 未匹配人脸 -->
        <a-tab-pane key="unmatched" title="未匹配人脸">
          <FaceUnmatchedTab
            v-model:assign-kind="assignKind"
            v-model:assign-person-id="assignPersonId"
            :items="unmatchedItems"
            :loading="unmatchedLoading"
            :page="unmatchedPage"
            :total="unmatchedTotal"
            :checked="unmatchedChecked"
            :assign-options="assignOptions"
            :assign-loading="assignLoading"
            :assigning="assigning"
            :filter-option="filterOption"
            @load-assign-options="loadAssignOptions"
            @assign="assignUnmatched"
            @page-change="onUnmatchedPageChange"
            @toggle-checked="toggleUnmatchedChecked"
            @open-inspiration="goDetail"
          />
        </a-tab-pane>

        <!-- 聚合分组（未匹配人脸按疑似同一人聚类） -->
        <a-tab-pane key="cluster" title="聚合分组">
          <FaceClusterTab
            v-model:cluster-assign-kind="clusterAssignKind"
            v-model:cluster-assign-person-id="clusterAssignPersonId"
            :cluster-task="clusterTask"
            :cluster-groups="clusterGroups"
            :cluster-total="clusterTotal"
            :cluster-page="clusterPage"
            :cluster-loading="clusterLoading"
            :clustering="clustering"
            :expanded-group-id="expandedGroupId"
            :group-detail-items="groupDetailItems"
            :group-detail-total="groupDetailTotal"
            :group-detail-page="groupDetailPage"
            :group-detail-loading="groupDetailLoading"
            :group-checked="groupChecked"
            :group-action-busy="groupActionBusy"
            :assign-options="assignOptions"
            :assign-loading="assignLoading"
            :busy="busy"
            :filter-option="filterOption"
            @load-assign-options="loadAssignOptions"
            @start-cluster="startCluster"
            @toggle-group-detail="toggleGroupDetail"
            @assign-group="assignGroup"
            @load-group-detail-page="onGroupDetailPageChange"
            @select-all-group="selectAllGroup"
            @clear-group-checked="clearGroupChecked"
            @assign-checked-group="assignCheckedGroup"
            @page-change="onClusterPageChange"
            @toggle-group-checked="toggleGroupChecked"
            @open-inspiration="goDetail"
          />
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<style scoped>
/* 结果区（人物列表/明细网格/角标/指派栏）的样式随模板拆到子组件，已迁到
   styles/faceScan.css（scoped 不跨组件生效）；这里只留本视图自身标记的样式。 */
.face-scan-page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 16px;
}

.task-card {
  margin-bottom: 16px;
}

.task-line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
</style>
