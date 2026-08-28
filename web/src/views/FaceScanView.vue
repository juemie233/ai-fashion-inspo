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
import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { IconLock } from '@arco-design/web-vue/es/icon'
import { bloggersApi, modelsApi } from '@/api/persons'
import {
  confirmFaceScan,
  fetchFaceClusterDetections,
  fetchFaceClusterGroups,
  fetchFaceClusterTask,
  fetchFaceScanResults,
  fetchFaceScanTask,
  runFaceCluster,
  runFaceMatch,
  startFaceScan,
  type DetectionItem,
  type FaceClusterGroup,
  type FaceClusterGroups,
  type FaceScanTaskOut,
  type PersonAggregateItem,
} from '@/api/faceScan'
import { getFileUrl } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import StatusTag from '@/components/common/StatusTag.vue'

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
let pollTimer: number | null = null

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

/** 轮询任务直到终态（3s 间隔；任务完成后刷新结果区） */
async function pollUntilIdle(taskId: number) {
  while (true) {
    await new Promise((r) => setTimeout(r, 3000))
    const status = await fetchFaceScanTask()
    scanTask.value = status.scan_task
    matchTask.value = status.match_task
    const current = [status.scan_task, status.match_task].find((t) => t?.id === taskId)
    if (!current || !['running', 'pending'].includes(current.status)) {
      await refreshAll()
      return
    }
  }
}

// ── 结果区：聚合（按人物）──
const resultTab = ref<'pending' | 'confirmed' | 'unmatched'>('pending')
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
const clusterTask = ref<FaceScanTaskOut | null>(null)
const clusterGroups = ref<FaceClusterGroup[]>([])
const clusterTotal = ref(0)
const clusterPage = ref(1)
const clusterSummary = ref<FaceClusterGroups['summary']>(null)
const clusterLoading = ref(false)
const clustering = ref(false)
// 展开的组：group_id → 明细分页状态
const expandedGroupId = ref<number | null>(null)
const groupDetailItems = ref<DetectionItem[]>([])
const groupDetailTotal = ref(0)
const groupDetailPage = ref(1)
const groupDetailLoading = ref(false)
const groupChecked = ref<Set<number>>(new Set())
const groupActionBusy = ref(false)
// 整组指派目标（复用未匹配区的人物选择器状态）
const clusterAssignKind = ref<'blogger' | 'model'>('blogger')
const clusterAssignPersonId = ref<number | undefined>(undefined)

/** 拉取最近聚类任务状态 */
async function loadClusterTask() {
  try {
    const { cluster_task } = await fetchFaceClusterTask()
    clusterTask.value = cluster_task
  } catch {
    /* 静默：聚类未运行过时不报错 */
  }
}

/** 开始人脸聚合聚类 */
async function startCluster() {
  clustering.value = true
  try {
    const { task_id, message } = await runFaceCluster()
    Message.success(message)
    await loadClusterTask()
    void pollClusterUntilIdle(task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建聚类任务失败'))
  } finally {
    clustering.value = false
  }
}

/** 轮询聚类任务直到终态，完成后刷新分组 */
async function pollClusterUntilIdle(taskId: number) {
  while (true) {
    await new Promise((r) => setTimeout(r, 2000))
    await loadClusterTask()
    const current = clusterTask.value
    if (!current || current.id !== taskId || !['running', 'pending'].includes(current.status)) {
      await loadClusterGroups()
      return
    }
  }
}

/** 加载聚合分组（分页） */
async function loadClusterGroups() {
  clusterLoading.value = true
  try {
    const data = await fetchFaceClusterGroups({ page: clusterPage.value, size: 20 })
    clusterGroups.value = data.items
    clusterTotal.value = data.total
    clusterSummary.value = data.summary
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载聚合分组失败'))
  } finally {
    clusterLoading.value = false
  }
}

/** 展开/收起某分组：加载组内人脸明细 */
async function toggleGroupDetail(group: FaceClusterGroup) {
  if (expandedGroupId.value === group.group_id) {
    expandedGroupId.value = null
    groupChecked.value = new Set()
    return
  }
  expandedGroupId.value = group.group_id
  groupChecked.value = new Set()
  groupDetailPage.value = 1
  await loadGroupDetail()
}

async function loadGroupDetail() {
  if (expandedGroupId.value === null) return
  groupDetailLoading.value = true
  try {
    const data = await fetchFaceClusterDetections(expandedGroupId.value, {
      page: groupDetailPage.value,
      size: 50,
    })
    groupDetailItems.value = data.items
    groupDetailTotal.value = data.total
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载组内人脸失败'))
  } finally {
    groupDetailLoading.value = false
  }
}

/** 整组指派给所选人物（复用 confirm 批量指派链路） */
async function assignGroup(group: FaceClusterGroup) {
  if (!clusterAssignPersonId.value) {
    Message.warning('请选择要指派的人物')
    return
  }
  const targetIds = group.detection_ids ?? []
  if (targetIds.length === 0) {
    Message.warning('该组没有可指派的人脸')
    return
  }
  groupActionBusy.value = true
  try {
    const result = await confirmFaceScan(
      'confirm',
      targetIds.map((id) => ({
        detection_id: id,
        person_type: clusterAssignKind.value,
        person_id: clusterAssignPersonId.value,
      })),
    )
    Message.success(
      `已整组指派 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
    )
    groupChecked.value.clear()
    await Promise.all([loadClusterGroups(), loadUnmatched()])
  } catch (e) {
    Message.error(getApiErrorMessage(e, '整组指派失败'))
  } finally {
    groupActionBusy.value = false
  }
}

/** 指派勾选的人脸给所选人物（部分选择后指定博主） */
async function assignCheckedGroup() {
  if (groupChecked.value.size === 0) {
    Message.warning('请先勾选要指派的人脸')
    return
  }
  if (!clusterAssignPersonId.value) {
    Message.warning('请选择要指派的人物')
    return
  }
  groupActionBusy.value = true
  try {
    const result = await confirmFaceScan(
      'confirm',
      [...groupChecked.value].map((id) => ({
        detection_id: id,
        person_type: clusterAssignKind.value,
        person_id: clusterAssignPersonId.value,
      })),
    )
    Message.success(
      `已指派勾选 ${result.confirmed} 条${result.skipped ? `（跳过 ${result.skipped} 条）` : ''}`,
    )
    groupChecked.value.clear()
    await Promise.all([loadClusterGroups(), loadUnmatched()])
  } catch (e) {
    Message.error(getApiErrorMessage(e, '指派勾选失败'))
  } finally {
    groupActionBusy.value = false
  }
}

/** 全选当前组全部人脸（跨分页拉全量后勾选；非仅当前页） */
async function selectAllGroup(group: FaceClusterGroup) {
  const allIds = group.detection_ids ?? []
  if (allIds.length === 0) {
    Message.warning('该组没有可勾选的人脸')
    return
  }
  groupChecked.value = new Set(allIds)
  Message.success(`已全选该组 ${allIds.length} 张人脸`)
}

/** 清空当前组勾选 */
function clearGroupChecked() {
  groupChecked.value = new Set()
}

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
onMounted(async () => {
  await refreshTasks()
  await refreshAll()
  await loadClusterGroups()
  await loadAssignOptions()
  pollTimer = window.setInterval(() => {
    if (busy.value) void refreshTasks()
  }, 3000)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
})

const router = useRouter()

/** 缩略图地址（优先缩略图，无则原图） */
function thumbUrl(item: DetectionItem): string {
  return getFileUrl(item.thumbnail_path || item.file_path)
}

/** 人物头像地址：人脸小图（自动裁剪）→ 手动头像，均无则显示首字（与人物列表/详情约定一致） */
function personAvatarUrl(item: PersonAggregateItem): string | undefined {
  const path = item.face_thumb_path || item.avatar_path
  return path ? getFileUrl(path) : undefined
}

/** 聚合分组代表图地址（优先缩略图，无则原图；无任何图时返回空串） */
function groupThumbUrl(group: FaceClusterGroup): string {
  const path = group.rep_thumbnail_path || group.rep_file_path
  return path ? getFileUrl(path) : ''
}

/** 点击素材缩略图跳转素材详情页 */
function goDetail(inspirationId: string) {
  router.push(`/detail/${inspirationId}`)
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
          <a-spin :loading="personsLoading" style="display: block">
            <div v-if="pendingPersons.length > 0" class="person-list">
              <div
                v-for="p in pendingPersons"
                :key="`${p.person_type}:${p.person_id}`"
                class="person-row"
              >
                <div class="person-head" @click="toggleDetail(p)">
                  <a-avatar :size="32">
                    <img v-if="personAvatarUrl(p)" :src="personAvatarUrl(p)" alt="" />
                    <template v-else>{{ p.name.slice(0, 1) }}</template>
                  </a-avatar>
                  <span class="person-name">{{ p.name }}</span>
                  <a-tag size="small" :color="p.person_type === 'blogger' ? 'arcoblue' : 'purple'">
                    {{ p.person_type === 'blogger' ? '穿搭博主' : '职业模特' }}
                  </a-tag>
                  <a-typography-text type="secondary" style="font-size: 12px">
                    {{ p.count }} 条候选 · 最高 {{ (p.best_conf ?? 0).toFixed(2) }}
                  </a-typography-text>
                </div>
                <a-space :size="6">
                  <a-button
                    size="mini"
                    type="primary"
                    :loading="detailActionBusy"
                    @click.stop="actOnPerson(p, 'confirm', true)"
                  >
                    确认高分（≥0.75）
                  </a-button>
                  <a-button
                    size="mini"
                    type="primary"
                    :loading="detailActionBusy"
                    @click.stop="actOnPerson(p, 'confirm')"
                  >
                    全部确认
                  </a-button>
                  <a-button
                    size="mini"
                    status="danger"
                    :loading="detailActionBusy"
                    @click.stop="actOnPerson(p, 'reject')"
                  >
                    全部驳回
                  </a-button>
                </a-space>

                <!-- 展开明细 -->
                <div v-if="detailKey === `${p.person_type}:${p.person_id}`" class="detail-block">
                  <a-spin :loading="detailLoading" style="display: block">
                    <div
                      v-if="detailItems.length > 0"
                      class="detail-grid"
                      :style="{ gridTemplateColumns: `repeat(${gridColumns}, 1fr)` }"
                    >
                      <div
                        v-for="item in detailItems"
                        :key="item.detection_id"
                        class="detail-item"
                        @click="goDetail(item.inspiration_id)"
                      >
                        <HoverImagePreview
                          class="image-wrap"
                          :large-src="getFileUrl(item.file_path)"
                        >
                          <img :src="thumbUrl(item)" loading="lazy" />
                        </HoverImagePreview>
                        <a-checkbox
                          class="detail-check"
                          :model-value="detailChecked.has(item.detection_id)"
                          @click.stop
                          @change="(v: unknown) => toggleDetailChecked(item.detection_id, v)"
                        />
                        <span class="detail-conf">{{ (item.confidence ?? 0).toFixed(2) }}</span>
                      </div>
                    </div>
                    <a-empty v-else description="该人物暂无候选明细" size="small" />
                  </a-spin>
                  <div class="detail-actions">
                    <a-space :size="8">
                      <a-radio-group v-model="gridColumns" type="button" size="mini">
                        <a-radio :value="3">3 列</a-radio>
                        <a-radio :value="4">4 列</a-radio>
                        <a-radio :value="5">5 列</a-radio>
                        <a-radio :value="6">6 列</a-radio>
                      </a-radio-group>
                      <a-pagination
                        v-if="detailTotal > 50"
                        size="mini"
                        :current="detailPage"
                        :page-size="50"
                        :total="detailTotal"
                        @change="
                          (p: number) => {
                            detailPage = p
                            loadDetail()
                          }
                        "
                      />
                    </a-space>
                    <a-space :size="6">
                      <a-button
                        size="mini"
                        :disabled="detailItems.length === 0"
                        @click="toggleSelectAllDetail"
                      >
                        {{
                          detailItems.length > 0 &&
                          detailItems.every((i) => detailChecked.has(i.detection_id))
                            ? '取消全选'
                            : '全选'
                        }}
                      </a-button>
                      <a-button
                        size="mini"
                        type="primary"
                        :loading="detailActionBusy"
                        @click="actOnChecked('confirm')"
                      >
                        确认勾选
                      </a-button>
                      <a-button
                        size="mini"
                        status="danger"
                        :loading="detailActionBusy"
                        @click="actOnChecked('reject')"
                      >
                        驳回勾选
                      </a-button>
                    </a-space>
                  </div>
                </div>
              </div>
            </div>
            <a-empty v-else description="暂无待审核候选，先运行扫描与匹配" />
            <a-pagination
              v-if="pendingTotal > 50"
              style="margin-top: 12px; justify-content: center"
              :current="pendingPage"
              :page-size="50"
              :total="pendingTotal"
              @change="
                (p: number) => {
                  pendingPage = p
                  loadAggregates()
                }
              "
            />
          </a-spin>
        </a-tab-pane>

        <!-- 已确认 -->
        <a-tab-pane key="confirmed" title="已确认">
          <a-spin :loading="personsLoading" style="display: block">
            <div v-if="confirmedPersons.length > 0" class="person-list">
              <div
                v-for="p in confirmedPersons"
                :key="`${p.person_type}:${p.person_id}`"
                class="person-row"
              >
                <div class="person-head" @click="toggleDetail(p)">
                  <a-avatar :size="32">
                    <img v-if="personAvatarUrl(p)" :src="personAvatarUrl(p)" alt="" />
                    <template v-else>{{ p.name.slice(0, 1) }}</template>
                  </a-avatar>
                  <span class="person-name">{{ p.name }}</span>
                  <a-tag size="small" color="green">
                    {{ p.person_type === 'blogger' ? '穿搭博主' : '职业模特' }}
                  </a-tag>
                  <a-typography-text type="secondary" style="font-size: 12px">
                    {{ p.count }} 条已确认
                  </a-typography-text>
                </div>
                <div v-if="detailKey === `${p.person_type}:${p.person_id}`" class="detail-block">
                  <a-spin :loading="detailLoading" style="display: block">
                    <div v-if="detailItems.length > 0" class="detail-grid">
                      <div
                        v-for="item in detailItems"
                        :key="item.detection_id"
                        class="detail-item locked-item"
                        @click="goDetail(item.inspiration_id)"
                      >
                        <HoverImagePreview
                          class="image-wrap"
                          :large-src="getFileUrl(item.file_path)"
                        >
                          <img :src="thumbUrl(item)" loading="lazy" />
                        </HoverImagePreview>
                        <!-- 已确认锁定：锁图标替代勾选框，不可撤销/编辑 -->
                        <span class="detail-lock"><IconLock /></span>
                        <span class="detail-conf">{{ (item.confidence ?? 0).toFixed(2) }}</span>
                      </div>
                    </div>
                    <a-empty v-else description="该人物暂无已确认明细" size="small" />
                  </a-spin>
                  <div class="detail-actions">
                    <a-pagination
                      v-if="detailTotal > 50"
                      size="mini"
                      :current="detailPage"
                      :page-size="50"
                      :total="detailTotal"
                      @change="
                        (p: number) => {
                          detailPage = p
                          loadDetail()
                        }
                      "
                    />
                    <a-typography-text type="secondary" style="font-size: 12px">
                      已确认关联已锁定，不可修改或撤销
                    </a-typography-text>
                  </div>
                </div>
              </div>
            </div>
            <a-empty v-else description="暂无已确认关联" />
            <a-pagination
              v-if="confirmedTotal > 50"
              style="margin-top: 12px; justify-content: center"
              :current="confirmedPage"
              :page-size="50"
              :total="confirmedTotal"
              @change="
                (p: number) => {
                  confirmedPage = p
                  loadAggregates()
                }
              "
            />
          </a-spin>
        </a-tab-pane>

        <!-- 未匹配人脸 -->
        <a-tab-pane key="unmatched" title="未匹配人脸">
          <div class="assign-bar">
            <a-radio-group
              v-model="assignKind"
              type="button"
              size="small"
              @change="loadAssignOptions"
            >
              <a-radio value="blogger">穿搭博主</a-radio>
              <a-radio value="model">职业模特</a-radio>
            </a-radio-group>
            <a-select
              v-model="assignPersonId"
              :options="assignOptions"
              :loading="assignLoading"
              placeholder="选择要指派的人物"
              size="small"
              style="width: 240px"
              allow-search
              :filter-option="filterOption"
            />
            <a-button size="small" type="primary" :loading="assigning" @click="assignUnmatched">
              指派勾选（{{ unmatchedChecked.size }}）
            </a-button>
          </div>
          <a-spin :loading="unmatchedLoading" style="display: block">
            <div v-if="unmatchedItems.length > 0" class="detail-grid unmatched-grid">
              <div
                v-for="item in unmatchedItems"
                :key="item.detection_id"
                class="detail-item"
                @click="goDetail(item.inspiration_id)"
              >
                <HoverImagePreview class="image-wrap" :large-src="getFileUrl(item.file_path)">
                  <img :src="thumbUrl(item)" loading="lazy" />
                </HoverImagePreview>
                <a-checkbox
                  class="detail-check"
                  :model-value="unmatchedChecked.has(item.detection_id)"
                  @click.stop
                  @change="(v: unknown) => toggleUnmatchedChecked(item.detection_id, v)"
                />
              </div>
            </div>
            <a-empty v-else description="暂无未匹配人脸" />
            <a-pagination
              v-if="unmatchedTotal > 50"
              style="margin-top: 12px; justify-content: center"
              :current="unmatchedPage"
              :page-size="50"
              :total="unmatchedTotal"
              @change="
                (p: number) => {
                  unmatchedPage = p
                  loadUnmatched()
                }
              "
            />
          </a-spin>
        </a-tab-pane>

        <!-- 聚合分组（未匹配人脸按疑似同一人聚类） -->
        <a-tab-pane key="cluster" title="聚合分组">
          <div class="assign-bar">
            <a-radio-group
              v-model="clusterAssignKind"
              type="button"
              size="small"
              @change="loadAssignOptions"
            >
              <a-radio value="blogger">穿搭博主</a-radio>
              <a-radio value="model">职业模特</a-radio>
            </a-radio-group>
            <a-select
              v-model="clusterAssignPersonId"
              :options="assignOptions"
              :loading="assignLoading"
              placeholder="选择要指派的人物（整组）"
              size="small"
              style="width: 240px"
              allow-search
              :filter-option="filterOption"
            />
            <a-button
              size="small"
              type="primary"
              :loading="clustering"
              :disabled="busy"
              @click="startCluster"
            >
              开始聚合聚类
            </a-button>
          </div>

          <a-spin :loading="clusterLoading" style="display: block">
            <!-- 聚类任务状态 -->
            <div v-if="clusterTask" class="task-line">
              聚类 <StatusTag :status="clusterTask.status" />
              <a-progress
                v-if="['running', 'pending'].includes(clusterTask.status)"
                :percent="clusterTask.progress / 100"
                size="small"
                style="width: 320px"
              />
              <a-typography-text type="secondary" style="font-size: 12px">
                <template v-if="clusterTask.result?.total_faces !== undefined">
                  共 {{ clusterTask.result.total_faces }} 张未匹配人脸
                  <template v-if="clusterTask.result.group_count !== undefined">
                    · 聚类出 {{ clusterTask.result.group_count }} 组
                  </template>
                  <template v-if="clusterTask.result.singletons !== undefined">
                    · {{ clusterTask.result.singletons }} 张人脸
                  </template>
                </template>
              </a-typography-text>
            </div>
            <a-typography-text v-else type="secondary" style="font-size: 12px">
              尚未运行过聚合聚类。点击「开始聚合聚类」，按相似度以平均链接策略把未匹配人脸分组
              （组间平均相似度达标才合并，防止不同人被链成巨组），便于整组指派给同一位博主/模特。
            </a-typography-text>
            <a-typography-text v-if="clusterTask?.error" type="danger" style="font-size: 12px">
              {{ clusterTask.error }}
            </a-typography-text>

            <!-- 分组列表 -->
            <div v-if="clusterGroups.length > 0" class="person-list" style="margin-top: 12px">
              <div v-for="g in clusterGroups" :key="g.group_id" class="person-row">
                <div class="person-head" @click="toggleGroupDetail(g)">
                  <img
                    v-if="g.rep_file_path"
                    :src="groupThumbUrl(g)"
                    class="group-rep-img"
                    :alt="`组${g.group_id}`"
                  />
                  <a-avatar v-else :size="32">{{ g.size }}</a-avatar>
                  <span class="person-name">疑似同一人 · {{ g.size }} 张人脸</span>
                  <a-typography-text type="secondary" style="font-size: 12px">
                    组 #{{ g.group_id }}
                  </a-typography-text>
                </div>
                <a-space :size="6">
                  <a-button
                    size="mini"
                    type="primary"
                    :loading="groupActionBusy"
                    :disabled="!clusterAssignPersonId"
                    @click.stop="assignGroup(g)"
                  >
                    整组指派
                  </a-button>
                  <a-button size="mini" @click.stop="toggleGroupDetail(g)">
                    {{ expandedGroupId === g.group_id ? '收起' : '查看人脸' }}
                  </a-button>
                </a-space>

                <!-- 展开：组内人脸网格 + 勾选指派 -->
                <div v-if="expandedGroupId === g.group_id" class="detail-block">
                  <a-spin :loading="groupDetailLoading" style="display: block">
                    <div v-if="groupDetailItems.length > 0" class="detail-grid">
                      <div
                        v-for="item in groupDetailItems"
                        :key="item.detection_id"
                        class="detail-item"
                        @click="goDetail(item.inspiration_id)"
                      >
                        <HoverImagePreview
                          class="image-wrap"
                          :large-src="getFileUrl(item.file_path)"
                        >
                          <img :src="thumbUrl(item)" loading="lazy" />
                        </HoverImagePreview>
                        <a-checkbox
                          class="detail-check"
                          :model-value="groupChecked.has(item.detection_id)"
                          @click.stop
                          @change="(v: unknown) => toggleGroupChecked(item.detection_id, v)"
                        />
                        <span v-if="item.confidence !== null" class="detail-conf">
                          {{ item.confidence.toFixed(2) }}
                        </span>
                      </div>
                    </div>
                    <a-empty v-else description="该组暂无明细" size="small" />
                  </a-spin>
                  <div class="detail-actions">
                    <a-space :size="8">
                      <a-pagination
                        v-if="groupDetailTotal > 50"
                        size="mini"
                        :current="groupDetailPage"
                        :page-size="50"
                        :total="groupDetailTotal"
                        @change="
                          (p: number) => {
                            groupDetailPage = p
                            loadGroupDetail()
                          }
                        "
                      />
                      <a-typography-text type="secondary" style="font-size: 12px">
                        已勾选 {{ groupChecked.size }} / {{ g.size }} 张
                      </a-typography-text>
                    </a-space>
                    <a-space :size="6">
                      <a-button
                        size="mini"
                        :disabled="groupDetailLoading"
                        @click="selectAllGroup(g)"
                      >
                        全选
                      </a-button>
                      <a-button
                        size="mini"
                        :disabled="groupChecked.size === 0"
                        @click="clearGroupChecked"
                      >
                        清空
                      </a-button>
                      <a-button
                        size="mini"
                        type="primary"
                        :loading="groupActionBusy"
                        :disabled="groupChecked.size === 0 || !clusterAssignPersonId"
                        @click="assignCheckedGroup"
                      >
                        指派勾选（{{ groupChecked.size }}）
                      </a-button>
                    </a-space>
                  </div>
                </div>
              </div>
            </div>
            <a-empty
              v-else-if="!clusterLoading && !clusterTask"
              description="暂无聚合分组，先运行「开始聚合聚类」"
            />
            <a-pagination
              v-if="clusterTotal > 20"
              style="margin-top: 12px; justify-content: center"
              :current="clusterPage"
              :page-size="20"
              :total="clusterTotal"
              @change="
                (p: number) => {
                  clusterPage = p
                  loadClusterGroups()
                }
              "
            />
          </a-spin>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<style scoped>
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

.person-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.person-row {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 8px 12px;
}

.person-head {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  flex: 1;
}

.person-name {
  font-weight: 600;
  font-size: 14px;
}

.detail-block {
  margin-top: 10px;
  border-top: 1px dashed #e5e7eb;
  padding-top: 10px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
}

.detail-item {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid #eef0f3;
  cursor: pointer;
}

.detail-item img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
}

.detail-item .arco-checkbox {
  position: absolute;
  top: 4px;
  left: 4px;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 4px;
  padding: 2px;
}

/* 已确认锁定标识：左上角锁图标（替代勾选框） */
.detail-lock {
  position: absolute;
  top: 4px;
  left: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  color: #009a29;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 4px;
}

.detail-conf {
  position: absolute;
  bottom: 4px;
  right: 4px;
  font-size: 11px;
  color: #fff;
  background: rgba(0, 0, 0, 0.55);
  border-radius: 4px;
  padding: 1px 5px;
}

.detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

.assign-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

/* 聚合分组代表图：组头像（人脸缩略图） */
.group-rep-img {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}

.unmatched-grid {
  min-height: 80px;
}

@media (max-width: 900px) {
  .detail-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
