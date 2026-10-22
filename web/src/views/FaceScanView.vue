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
import {
  fetchFaceScanResults,
  fetchFaceScanTask,
  type FaceScanTaskOut,
  type PersonAggregateItem,
} from '@/api/faceScan'
import { getApiErrorMessage } from '@/utils/apiError'
import { openInspiration } from '@/utils/openInspiration'
import { useFaceClusterGroups } from '@/composables/useFaceClusterGroups'
import { useFaceServiceStatus } from '@/composables/useFaceServiceStatus'
import { usePolling } from '@/composables/usePolling'
import { useFaceScanActions } from '@/composables/useFaceScanActions'
import { useFaceUnmatchedAssign } from '@/composables/useFaceUnmatchedAssign'
import { useFaceDetailPanel, type ResultTab } from '@/composables/useFaceDetailPanel'
import FaceClusterTab from '@/components/person/face/FaceClusterTab.vue'
import FaceConfirmedTab from '@/components/person/face/FaceConfirmedTab.vue'
import FacePendingTab from '@/components/person/face/FacePendingTab.vue'
import FaceUnmatchedTab from '@/components/person/face/FaceUnmatchedTab.vue'
import FaceScanTaskCard from '@/components/person/FaceScanTaskCard.vue'
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

// 人脸子服务可用性：离线时**事前**提示并禁用动作，而不是点了按钮才拿到 503。
// 探测与结果缓存在 composable 内，人物详情页的人脸注册面板共用同一次探测。
const {
  status: faceService,
  offline: faceServiceOffline,
  load: loadFaceServiceStatus,
} = useFaceServiceStatus()

/** 手动复检子服务中（离线提示条的「重新检测」按钮 loading） */
const checkingFaceService = ref(false)

/** 手动复检：供用户在启动 face-service 后立刻恢复，不必等自动复检或刷新页面 */
async function recheckFaceService() {
  checkingFaceService.value = true
  try {
    await loadFaceServiceStatus()
    if (faceServiceOffline.value) {
      Message.warning(faceService.value?.message || '仍未探测到人脸识别子服务')
    } else {
      Message.success('人脸识别子服务已可用')
    }
  } finally {
    checkingFaceService.value = false
  }
}

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

// ── 明细（按人物展开 + 批量审核）：实现见 composables/useFaceDetailPanel.ts ──

const {
  detailKey,
  detailItems,
  detailPage,
  detailTotal,
  detailLoading,
  detailChecked,
  detailActionBusy,
  toggleDetail,
  loadDetail,
  actOnPerson,
  actOnChecked,
} = useFaceDetailPanel({ resultTab, refreshAll })

const { startScan, cancelTask, startMatch } = useFaceScanActions({
  scanTask,
  matchTask,
  scope,
  autoMatch,
  starting,
  cancelling,
  matching,
  detailChecked,
  refreshTasks,
  refreshAll,
})

/** 候选网格列数（3~6 可选，默认 6） */
const gridColumns = ref<number>(6)

// ── 未匹配区（实现见 composables/useFaceUnmatchedAssign.ts）──
// 同名解构，模板无需改动；唯一的跨域依赖「指派后整页刷新」以回调注入。
const {
  unmatchedItems,
  unmatchedPage,
  unmatchedTotal,
  unmatchedLoading,
  unmatchedChecked,
  assignKind,
  assignPersonId,
  assignOptions,
  assignLoading,
  assigning,
  loadUnmatched,
  loadAssignOptions,
  assignUnmatched,
} = useFaceUnmatchedAssign({ refreshAll })

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

// 子服务离线期间的自动复检：启动 face-service 属于用户手动操作，页面不刷新时
// 离线态会一直挂着（按钮永久禁用）。离线时每 15s 复检一次，恢复后自动解除
// 禁用；在线时不发请求（避免常驻轮询）。
const { start: startFaceServicePolling } = usePolling({
  intervalMs: 15000,
  immediate: false,
  callback: () => {
    if (faceServiceOffline.value) void loadFaceServiceStatus()
  },
})

onMounted(async () => {
  await loadFaceServiceStatus()
  await refreshTasks()
  await refreshAll()
  await loadClusterGroups()
  await loadAssignOptions()
  startTaskPolling()
  startFaceServicePolling()
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
    <!-- 子服务离线提示：事前给出原因，而不是等点了按钮才报 503 -->
    <a-alert
      v-if="faceServiceOffline"
      type="warning"
      class="face-service-alert"
      show-icon
      title="人脸识别子服务未启动或不可达"
    >
      <div>{{ faceService?.message }}</div>
      <div class="face-service-alert-tip">
        人脸相关动作（扫描 / 重匹配 / 注册 / 检测）已暂时禁用。启动 face-service
        后本页会自动恢复（离线期间每 15 秒复检一次），也可点「重新检测」立即探测； 若
        FACE_SERVICE_URL 未配置，则人脸功能整体不可用。
      </div>
      <a-button
        size="mini"
        class="face-service-alert-btn"
        :loading="checkingFaceService"
        @click="recheckFaceService"
      >
        重新检测
      </a-button>
    </a-alert>

    <!-- 任务卡片 -->
    <FaceScanTaskCard
      v-model:scope="scope"
      v-model:auto-match="autoMatch"
      :busy="busy"
      :offline="faceServiceOffline"
      :scan-task="scanTask"
      :match-task="matchTask"
      :starting="starting"
      :matching="matching"
      :cancelling="cancelling"
      @start-scan="startScan"
      @start-match="startMatch"
      @cancel="cancelTask"
    />

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

/* 子服务离线提示条 */
.face-service-alert {
  margin-bottom: 12px;
}

.face-service-alert-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #86909c;
  line-height: 1.6;
}

/* 手动复检按钮：贴在提示条末尾，不与提示文案同行 */
.face-service-alert-btn {
  margin-top: 8px;
}
</style>
