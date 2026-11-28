<script setup lang="ts">
/** 近似重复检测：感知哈希分组候选 + 弹窗逐组左右对比 + 人工确认删除。
 *
 * 扫描出近似重复组后自动打开对比弹窗：左右并排展示每组前两张素材，
 * 由用户决定保留哪一张（或都保留跳过）；全部处理完统一提交删除任务，
 * 物理删除冗余素材（复用批量删除任务 + 审计留痕）。
 * 若两张都不满意，可点「删除两张」立即提交删除当前两张并自动切到下一对候选。
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  useNearDuplicatesApi,
  type NearDuplicateFile,
  type NearDuplicateGroup,
  type NearDuplicateResult,
} from '@/composables/useNearDuplicatesApi'
import { getFileUrl } from '@/api/inspirations'
import { formatSize } from '@/utils/format'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  collectIdsToDelete,
  collectIdsToDeleteByKeeper,
  dropSubmittedFiles,
  nearDupScopeLabel,
  type DupDecision,
} from '@/utils/nearDup'
import { usePolling } from '@/composables/usePolling'

const nearApi = useNearDuplicatesApi()

const emit = defineEmits<{
  (e: 'delete-selected', ids: string[], done: (ok: boolean) => void): void
  /** 「删除两张」已提交批量删除任务，父组件接管进度轮询（任务完成后刷新统计） */
  (e: 'task-started', taskId: number, message: string): void
}>()

const threshold = ref(32)
// 默认全库扫描（limit=0）：phash 已缓存时为纯内存分组，万级素材秒级返回，
// 避免随机抽样把重复对漏检、反复扫都「未发现重复」的瘫痪体感
const limit = ref(0)
const scanning = ref(false)
const result = ref<NearDuplicateResult | null>(null)
/** 本次扫描实际用的抽样上限（下拉框后续改动不能改写结果文案） */
const scannedLimit = ref(0)

const thresholdOptions = [
  { label: '严格（差 16 位 / 768 位）', value: 16 },
  { label: '标准（差 32 位，推荐）', value: 32 },
  { label: '宽松（差 64 位）', value: 64 },
]

const limitOptions = [
  { label: '全库扫描', value: 0 },
  { label: '随机 500 张', value: 500 },
  { label: '随机 1000 张', value: 1000 },
  { label: '随机 2000 张', value: 2000 },
  { label: '随机 5000 张', value: 5000 },
]

const groups = computed(() => result.value?.groups ?? [])

/**
 * 组的稳定键：`keeper_id` + 组内文件 id 排序串。
 *
 * 决策 Map 不能拿 NearDuplicateGroup 对象引用当键：重新扫描/列表重排会生成新对象，
 * 引用一换决定就全丢（用户已选过的组又变回未决定）。用组内容算键，只要这一组的
 * 素材集合没变，重扫/重排后仍指向同一组。
 */
function groupKey(group: NearDuplicateGroup): string {
  const ids = group.files.map((f) => f.id).sort()
  return `${group.keeper_id}:${ids.join(',')}`
}

// ── 弹窗逐组对比状态 ──
const showDupModal = ref(false)
/** 待处理的重复组队列（按扫描结果顺序） */
const dupGroups = ref<NearDuplicateGroup[]>([])
/** 当前组下标（0 起） */
const dupIndex = ref(0)
/**
 * 逐组的保留决策（稳定组键 → 决策）。
 *
 * 为什么记「决策」而不是只累加待删 ID：这样才能来回翻页改主意——改选时该组旧决策对应的
 * ID 会被自动撤掉（见 deletingIds），最终提交的就是用户最后看到的那批；只累加 ID 的话
 * 「点快了误删」无法挽回。
 *
 * 键是 :func:`groupKey` 而不是组对象：重扫/重排产生的同内容新对象也能命中同一决定。
 */
const dupDecisions = ref<Map<string, DupDecision>>(new Map())
/** 稳定组键 → 指定保留的素材 ID（支持保留组内任意一张，不只前两张） */
const dupKeepers = ref<Map<string, string>>(new Map())
/** 全部处理完成（进入提交确认视图） */
const allDone = ref(false)
/** 提交删除后的提示：此刻素材正在后台删除，不能说「未发现近似重复」 */
const submittedNotice = ref('')

/** 当前组 */
const currentGroup = computed<NearDuplicateGroup | null>(
  () => dupGroups.value[dupIndex.value] ?? null,
)
/** 当前组的稳定键（未选中组为 null） */
const currentGroupKey = computed<string | null>(() => {
  const group = currentGroup.value
  return group ? groupKey(group) : null
})
/** 当前组左边素材（组内第一张，评分最高者） */
const leftFile = computed<NearDuplicateFile | null>(() => currentGroup.value?.files[0] ?? null)
/** 当前组右边素材（组内第二张） */
const rightFile = computed<NearDuplicateFile | null>(() => currentGroup.value?.files[1] ?? null)
/** 当前组已做的决策（未决定为 null） */
const currentDecision = computed<DupDecision | null>(() => {
  const key = currentGroupKey.value
  return key ? (dupDecisions.value.get(key) ?? null) : null
})
/** 当前组指定保留的素材 ID（未指定为 null） */
const currentKeeperId = computed<string | null>(() => {
  const key = currentGroupKey.value
  return key ? (dupKeepers.value.get(key) ?? null) : null
})

/** 当前保留选择的中文说明（前两张保持「保留左边/右边」语义，其它显示 ID 前缀） */
const currentKeeperLabel = computed<string>(() => {
  const id = currentKeeperId.value
  if (!id) return ''
  if (id === leftFile.value?.id) return '保留左边'
  if (id === rightFile.value?.id) return '保留右边'
  return `保留 ${id.slice(0, 8)}…`
})

/** 待删素材 ID 集合：由各组当前决策现算，改主意后自动同步 */
const deletingIds = computed<Set<string>>(() => {
  const ids = new Set<string>()
  for (const g of dupGroups.value) {
    const key = groupKey(g)
    const keeperId = dupKeepers.value.get(key)
    if (keeperId) {
      for (const id of collectIdsToDeleteByKeeper(g, keeperId)) ids.add(id)
      continue
    }
    const decision = dupDecisions.value.get(key)
    if (!decision) continue
    for (const id of collectIdsToDelete(g, decision)) ids.add(id)
  }
  return ids
})
/** 已决定删除数量 */
const deleteCount = computed(() => deletingIds.value.size)
/** 已做出决策的组数（按稳定组键统计） */
const decidedCount = computed(
  () =>
    dupGroups.value.filter(
      (g) => dupDecisions.value.has(groupKey(g)) || dupKeepers.value.has(groupKey(g)),
    ).length,
)
/**
 * 本次待删除集合里的收藏素材。
 *
 * 收藏素材被物理删除后不可恢复，提交前必须显式二次确认（默认不静默删除）。
 */
const favoriteFilesToDelete = computed<NearDuplicateFile[]>(() => {
  const deleting = deletingIds.value
  const seen = new Set<string>()
  const files: NearDuplicateFile[] = []
  for (const group of dupGroups.value) {
    for (const file of group.files) {
      if (deleting.has(file.id) && file.is_favorite && !seen.has(file.id)) {
        seen.add(file.id)
        files.push(file)
      }
    }
  }
  return files
})
const favoriteDeleteCount = computed(() => favoriteFilesToDelete.value.length)
/** 能否上下翻页 */
const canPrev = computed(() => dupIndex.value > 0)
const canNext = computed(() => dupIndex.value < dupGroups.value.length - 1)

/** 建议保留素材（评分最高者）ID 前 8 位提示 */
const keeperHint = computed<string>(() => {
  const g = currentGroup.value
  if (!g) return ''
  const keeper = g.files.find((f) => f.id === g.keeper_id) ?? g.files[0]
  return keeper ? `${keeper.id.slice(0, 8)}…` : ''
})

async function scan() {
  scanning.value = true
  result.value = null
  submittedNotice.value = ''
  try {
    scannedLimit.value = limit.value
    result.value = await nearApi.scan(limit.value, threshold.value)
    if (result.value.groups.length === 0) {
      Message.success(`已${nearDupScopeLabel(result.value, limit.value)}，未发现近似重复`)
    } else {
      // 发现重复组 → 自动打开弹窗逐组处理
      openDupModal()
    }
    // 缓存没补齐时提示一次（扫描接口只做限时补算，缺口交给后台任务）
    if (result.value.missing > 0) {
      Message.warning(
        `哈希缓存还差 ${result.value.missing} 张未补齐：本次只覆盖已缓存部分，可点「后台补齐哈希缓存」一次补完`,
      )
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '近似重复扫描失败'))
  } finally {
    scanning.value = false
  }
}

// ── 哈希缓存后台补齐（扫描接口限时补算，剩余缺口交给 worker）──
const backfillBusy = ref(false)
const backfillTaskId = ref<number | null>(null)
const backfillProgress = ref(0)
const backfillStatus = ref('')
const backfillMessage = ref('')
/** 强制整库重算（哈希来源从原图换成缩略图后需要跑一次；平时别勾） */
const forceRehash = ref(false)

/** 任务轮询：3 秒一次，终态即停并提示（进度写进 backfillProgress） */
/** 轮询连续失败次数：连续 3 次只提示一次，避免每 3 秒刷 toast */
let backfillPollFailures = 0
const { start: startBackfillPolling, stop: stopBackfillPolling } = usePolling({
  intervalMs: 3000,
  immediate: true,
  callback: async () => {
    const id = backfillTaskId.value
    if (id === null) return
    try {
      const data = await nearApi.fetchBackfillTask(id)
      backfillPollFailures = 0
      backfillProgress.value = data.progress ?? 0
      backfillStatus.value = data.status
      if (!['pending', 'running'].includes(data.status)) {
        stopBackfillPolling()
        forceRehash.value = false
        if (data.status === 'failed' || data.status === 'cancelled') {
          backfillMessage.value =
            data.error || (data.status === 'cancelled' ? '补齐任务已取消' : '补齐任务失败')
          Message.error(backfillMessage.value)
          return
        }
        const complete = data.result?.complete ?? false
        backfillMessage.value = complete
          ? '哈希缓存已补齐，可重新扫描（现在覆盖全库）'
          : `补齐结束：仍有 ${data.result?.missing ?? 0} 张未缓存（其中 ${
              data.result?.unhashable ?? 0
            } 张文件缺失或格式不支持）`
        Message.success(backfillMessage.value)
      }
    } catch {
      // 连续失败才提示一次（不静默到底，也不每个 tick 弹）；任务详情在任务中心仍可查看
      backfillPollFailures += 1
      if (backfillPollFailures === 3) {
        Message.warning('补齐任务进度连续获取失败，请稍后在任务中心查看')
      }
    }
  },
})

/** 启动后台补齐任务（幂等：已有任务在跑时复用同一个） */
async function startBackfill() {
  backfillBusy.value = true
  try {
    const data = await nearApi.startBackfill(forceRehash.value)
    backfillTaskId.value = data.task_id
    backfillProgress.value = 0
    backfillMessage.value = data.message
    // 没有 task_id 表示缓存已完整/无需补齐：不要留下永不结束的 running 进度条
    backfillStatus.value = data.task_id !== null ? 'running' : 'done'
    Message.success(data.message)
    if (data.task_id !== null) startBackfillPolling()
  } catch (e) {
    // 缓存已完整时接口返回 400，按提示处理；其它错误（500/网络）必须报错
    const status = (e as { response?: { status?: number } }).response?.status
    const msg = getApiErrorMessage(e, '创建补齐任务失败')
    if (status === 400) Message.info(msg)
    else Message.error(msg)
  } finally {
    backfillBusy.value = false
  }
}

/** 打开逐组对比弹窗 */
function openDupModal() {
  dupGroups.value = [...groups.value]
  // 重扫/重排可能替换组对象：只保留仍存在于本次候选里的稳定键决策，
  // 同一组（素材集合不变）继续沿用用户已做的选择，新组则从未决定开始。
  const keys = new Set(dupGroups.value.map(groupKey))
  const pruneMap = <T,>(map: Map<string, T>): Map<string, T> =>
    new Map([...map].filter(([key]) => keys.has(key)))
  dupDecisions.value = pruneMap(dupDecisions.value)
  dupKeepers.value = pruneMap(dupKeepers.value)
  dupIndex.value = 0
  allDone.value = false
  showDupModal.value = true
}

/** 关闭对比弹窗（只收界面状态；删除决定由 :func:`submitDecisions` 负责提交） */
function closeDupModal() {
  showDupModal.value = false
  dupGroups.value = []
  dupDecisions.value = new Map()
  dupKeepers.value = new Map()
  clearPreloadedThumbnails()
}

/**
 * 决策后短暂忽略再次点击（毫秒）。
 *
 * 这是「点快了误删」的直接防线：做出决定会立刻切到下一组，手快连点两下就会把下一组
 * 也按同一个按钮决定掉（两张原本想保留的图被删）。300ms 只挡连点，不挡正常操作。
 */
const DECISION_GUARD_MS = 300
const decisionLocked = ref(false)
/** 连点保护定时器：卸载/翻页时清理，避免卸载后改状态 */
let decisionTimer: ReturnType<typeof setTimeout> | null = null

function clearDecisionTimer() {
  if (decisionTimer !== null) {
    clearTimeout(decisionTimer)
    decisionTimer = null
  }
}

/** 决策后短暂锁定按钮，防手快连点把下一组也删掉 */
function lockDecisionBriefly() {
  clearDecisionTimer()
  decisionLocked.value = true
  decisionTimer = setTimeout(() => {
    decisionTimer = null
    decisionLocked.value = false
  }, DECISION_GUARD_MS)
}

/**
 * 记录当前组的保留决策并切到下一组（已是最后一组则进入提交确认视图）。
 *
 * 同一组重复决策 = 改主意：直接覆盖，待删集合由 :data:`deletingIds` 现算，
 * 不会残留上一次决策的 ID。
 */
function decide(decision: DupDecision) {
  const g = currentGroup.value
  const key = currentGroupKey.value
  if (!g || !key || decisionLocked.value) return
  const next = new Map(dupDecisions.value)
  next.set(key, decision)
  dupDecisions.value = next
  const nextKeepers = new Map(dupKeepers.value)
  nextKeepers.delete(key)
  dupKeepers.value = nextKeepers
  lockDecisionBriefly()
  if (dupIndex.value < dupGroups.value.length - 1) {
    dupIndex.value += 1
  } else {
    allDone.value = true // 全部处理完，进入提交确认视图
  }
}

/** 上一组 / 下一组：只翻页回看，不改动任何决策（改主意请直接点按钮重选） */
function goPrev() {
  if (!canPrev.value) return
  dupIndex.value -= 1
  // 翻页是明确的动作：解除连点保护，否则翻回来立刻改主意会被 300ms 挡掉
  clearDecisionTimer()
  decisionLocked.value = false
}

function goNext() {
  if (!canNext.value) return
  dupIndex.value += 1
  clearDecisionTimer()
  decisionLocked.value = false
}

/** 决策的中文名（用于「本组已选：…」提示） */
function decisionLabel(decision: DupDecision): string {
  if (decision === 'keep-left') return '保留左边'
  if (decision === 'keep-right') return '保留右边'
  return '都保留（跳过本组）'
}

/** 指定保留组内某一张：其余全部删除（适用于组内 >2 张时选择任意一张） */
function decideKeeper(keeperId: string) {
  const g = currentGroup.value
  const key = currentGroupKey.value
  if (!g || !key || decisionLocked.value) return
  const nextKeepers = new Map(dupKeepers.value)
  nextKeepers.set(key, keeperId)
  dupKeepers.value = nextKeepers
  const nextDecisions = new Map(dupDecisions.value)
  nextDecisions.delete(key)
  dupDecisions.value = nextDecisions
  lockDecisionBriefly()
  if (dupIndex.value < dupGroups.value.length - 1) dupIndex.value += 1
  else allDone.value = true
}

/** 保留左边：删除该组其余全部素材 */
function keepLeft() {
  const id = leftFile.value?.id
  if (id) decideKeeper(id)
}

/** 保留右边：删除该组除右图外的全部素材 */
function keepRight() {
  const id = rightFile.value?.id
  if (id) decideKeeper(id)
}

/** 都保留（跳过本组） */
function skipGroup() {
  decide('skip')
}

/** 正在提交「删除两张」的删除任务 */
const deletingBoth = ref(false)

/**
 * 删除当前对比的两张素材：确认后立即提交批量删除任务（物理删除，复用
 * batch-delete 任务 + 审计留痕）。提交成功后将这两张从当前组移除：
 * 组内剩余 ≥2 张则继续对比该组剩余素材，否则整组移出候选队列并展示下一组；
 * 候选队列清空时显示「暂无更多近似重复素材」。提交失败仅提示错误，不改动界面状态。
 */
async function deleteBoth() {
  const g = currentGroup.value
  const left = leftFile.value
  const right = rightFile.value
  if (!g || !left || !right || deletingBoth.value) return
  deletingBoth.value = true
  try {
    const ids = collectIdsToDelete(g, 'delete-both')
    // 直接调用批量删除接口并等待结果：失败时不改动候选列表
    const data = await nearApi.submitBatchDelete(ids)
    emit('task-started', data.task_id, data.message)

    // 不可变更新：组对象同时被 result.groups 与 dupGroups 引用，就地 splice/filter
    // 会留下 files=0/1 的僵尸组。先算出新组，再同时替换两处。
    const remaining = g.files.filter((f) => f.id !== left.id && f.id !== right.id)
    const nextGroup = remaining.length >= 2 ? { ...g, files: remaining } : null
    const replaceGroup = (arr: NearDuplicateGroup[]) => {
      const index = arr.indexOf(g)
      if (index < 0) return arr
      if (!nextGroup) return arr.filter((item) => item !== g)
      const copy = [...arr]
      copy[index] = nextGroup
      return copy
    }
    dupGroups.value = replaceGroup(dupGroups.value)
    if (result.value) result.value.groups = replaceGroup(result.value.groups)
    // 组内容已变化：旧稳定键（以及新组的键）上的决策/保留者一律作废，避免套用到新素材对上
    const oldKey = groupKey(g)
    const nextKey = nextGroup ? groupKey(nextGroup) : null
    const nextDecisions = new Map(dupDecisions.value)
    nextDecisions.delete(oldKey)
    if (nextKey) nextDecisions.delete(nextKey)
    dupDecisions.value = nextDecisions
    const nextKeepers = new Map(dupKeepers.value)
    nextKeepers.delete(oldKey)
    if (nextKey) nextKeepers.delete(nextKey)
    dupKeepers.value = nextKeepers
    if (!nextGroup) {
      if (dupGroups.value.length === 0) {
        if (deletingIds.value.size > 0) allDone.value = true
      } else if (dupIndex.value >= dupGroups.value.length) {
        dupIndex.value = dupGroups.value.length - 1
      }
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除两张失败'))
  } finally {
    deletingBoth.value = false
  }
}

/**
 * 提交累计的删除决定：交给父组件（批量删除任务 + 审计留痕），并就地更新候选列表。
 *
 * **不立刻重扫**：删除是后台任务，提交瞬间素材还在库里，重扫会把刚处理过的组原样
 * 查回来 → 弹窗又自动打开、进度退回第 1 组（用户刚做完选择就被弹回选择页）。
 * 库里真实状态等任务跑完后由用户再扫一次确认。
 */
async function submitDecisions(): Promise<boolean> {
  const ids = new Set(deletingIds.value)
  if (ids.size === 0) return true
  // 等父组件把任务真正创建成功再改界面与提示；失败时保留全部决定供重试/放弃
  const ok = await new Promise<boolean>((resolve) => {
    emit('delete-selected', [...ids], resolve)
  })
  if (!ok) {
    Message.error('删除任务提交失败，决定仍保留；可重试或退出放弃')
    return false
  }
  Message.success(`已提交删除任务：${ids.size} 个冗余素材（后台物理删除）`)
  if (result.value) {
    result.value.groups = dropSubmittedFiles(result.value.groups, ids)
  }
  dupDecisions.value = new Map()
  dupKeepers.value = new Map()
  submittedNotice.value =
    groups.value.length > 0
      ? `已提交删除 ${ids.size} 个冗余素材（后台物理删除），列表已按决定更新，剩余 ${groups.value.length} 组待确认`
      : `已提交删除 ${ids.size} 个冗余素材（后台物理删除），删除完成后可重新扫描确认`
  return true
}

/**
 * 退出对比弹窗：有待提交决定时先让用户明确选择（提交 / 放弃 / 继续），
 * 不再把「退出」静默当提交；没有决定时直接关闭。
 */
const exitConfirmVisible = ref(false)

function requestExit() {
  if (deletingIds.value.size > 0) {
    exitConfirmVisible.value = true
    return
  }
  closeDupModal()
}

async function submitAndExit(): Promise<void> {
  if (await submitDecisions()) {
    exitConfirmVisible.value = false
    closeDupModal()
  }
}

function discardAndExit(): void {
  exitConfirmVisible.value = false
  closeDupModal()
}

/** 提交确认视图的「确认提交删除」 */
async function confirmSubmit(): Promise<void> {
  if (deletingIds.value.size === 0) {
    Message.info('没有需要删除的素材')
    closeDupModal()
    return
  }
  if (await submitDecisions()) closeDupModal()
}

// ── 收藏素材删除保护 ──
//
// 收藏是用户的强意图标记：待删集合里只要含收藏素材，提交前必须再确认一次，
// 且文案要明确说明「收藏素材也会被物理删除」。取消后决定保持原样，不产生删除。

type FavoritePendingAction = 'confirm-submit' | 'submit-exit'
const favoriteConfirmVisible = ref(false)
const pendingFavoriteAction = ref<FavoritePendingAction | null>(null)

/** 提交入口（确认页）：有收藏素材时转二次确认，否则直接提交 */
function requestConfirmSubmit(): void {
  if (favoriteDeleteCount.value > 0) {
    pendingFavoriteAction.value = 'confirm-submit'
    favoriteConfirmVisible.value = true
    return
  }
  void confirmSubmit()
}

/** 退出入口：有收藏素材时先确认再「提交并退出」 */
function requestSubmitAndExit(): void {
  if (favoriteDeleteCount.value > 0) {
    pendingFavoriteAction.value = 'submit-exit'
    favoriteConfirmVisible.value = true
    return
  }
  void submitAndExit()
}

/** 取消收藏素材确认：不提交、不改变已有决定，回到原视图继续处理 */
function cancelFavoriteConfirm(): void {
  favoriteConfirmVisible.value = false
  pendingFavoriteAction.value = null
  Message.info('已取消：删除决定仍保留，可继续调整后提交')
}

/** 确认删除含收藏素材：按进入确认前选择的动作继续提交 */
async function confirmFavoriteDelete(): Promise<void> {
  const action = pendingFavoriteAction.value
  favoriteConfirmVisible.value = false
  pendingFavoriteAction.value = null
  if (action === 'confirm-submit') await confirmSubmit()
  else if (action === 'submit-exit') await submitAndExit()
}

/** 「删除两张」按钮的二次确认文案：含收藏素材时要明确警告 */
const pairDeleteContent = computed<string>(() => {
  const favorites = [leftFile.value, rightFile.value].filter((file): file is NearDuplicateFile =>
    Boolean(file?.is_favorite),
  )
  if (favorites.length === 0) {
    return '确定要同时删除这两张素材吗？将物理删除（文件与记录不可恢复），确认后自动切换到下一组。'
  }
  return `当前两张中有 ${favorites.length} 个已收藏素材（★ 已收藏）。继续会物理删除收藏素材（文件与记录不可恢复），确定继续？`
})

function fileUrl(f: NearDuplicateFile): string {
  return getFileUrl(f.thumbnail_path || f.file_path)
}

/** 接口可能补带的展示字段（宽高/标签/评分）；旧后端没有时按 -- 展示 */
type FileDisplayExtra = {
  width?: unknown
  height?: unknown
  tags?: unknown
  tag_count?: unknown
  rating?: unknown
}

/** 缩略图条带每张的辅助信息：宽×高 · 标签数 · 评分（缺失显示 --） */
function displayFileMeta(file: NearDuplicateFile): string {
  const extra = file as NearDuplicateFile & FileDisplayExtra
  const width = typeof extra.width === 'number' ? extra.width : null
  const height = typeof extra.height === 'number' ? extra.height : null
  const dimensions = width !== null && height !== null ? `${width}×${height}` : '--'
  const tagCount = Array.isArray(extra.tags)
    ? extra.tags.length
    : typeof extra.tag_count === 'number'
      ? extra.tag_count
      : null
  const tags = tagCount !== null ? `${tagCount} 个标签` : '--'
  const rating = typeof extra.rating === 'number' ? `${extra.rating} 星` : '--'
  return `${dimensions} · ${tags} · ${rating}`
}

// ── 下一组缩略图预加载（当前组变化时只预取前两张，卸载清理引用）──

let preloadedImages: HTMLImageElement[] = []
const preloadedSrcs = new Set<string>()

function preloadNextGroupThumbnails() {
  const next = dupGroups.value[dupIndex.value + 1]
  if (!next) return
  for (const file of next.files.slice(0, 2)) {
    const src = getFileUrl(file.thumbnail_path || file.file_path)
    if (!src || preloadedSrcs.has(src) || typeof Image === 'undefined') continue
    preloadedSrcs.add(src)
    const image = new Image()
    image.decoding = 'async'
    image.src = src
    preloadedImages.push(image)
  }
}

function clearPreloadedThumbnails() {
  preloadedImages = []
  preloadedSrcs.clear()
}

watch([showDupModal, dupIndex, () => dupGroups.value.length], () => {
  if (showDupModal.value) preloadNextGroupThumbnails()
})

// ── 键盘快捷键：弹窗打开且焦点不在输入控件时生效 ──

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

function handleGlobalKeydown(event: KeyboardEvent): void {
  if (!showDupModal.value) return
  // 收藏确认/退出确认弹窗有各自的按钮；不在此处抢 Esc/方向键
  if (exitConfirmVisible.value || favoriteConfirmVisible.value) return
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (isEditableTarget(event.target)) return

  if (event.key === 'Escape') {
    event.preventDefault()
    requestExit()
    return
  }
  // 提交确认视图只允许 Esc 退出，不让方向键把界面拉回对比
  if (allDone.value || !currentGroup.value) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goPrev()
    return
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    goNext()
    return
  }
  if (event.key.toLowerCase() === 's') {
    event.preventDefault()
    skipGroup()
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleGlobalKeydown)
  preloadNextGroupThumbnails()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  clearDecisionTimer()
  clearPreloadedThumbnails()
})

/** 上传时间展示：MM-DD HH:mm（无时间返回 '-'） */
function fmtTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 收藏标记展示 */
function favoriteLabel(f: NearDuplicateFile): string {
  return f.is_favorite ? '★ 已收藏' : ''
}
</script>

<template>
  <a-card title="近似重复检测" size="small" class="near-dup-card">
    <template #extra>
      <a-space align="center">
        <a-select
          v-model="threshold"
          :options="thresholdOptions"
          size="small"
          style="width: 180px"
        />
        <a-select v-model="limit" :options="limitOptions" size="small" style="width: 130px" />
        <a-button size="small" type="primary" :loading="scanning" @click="scan"
          >扫描近似重复</a-button
        >
      </a-space>
    </template>

    <p style="color: var(--color-text-3); font-size: 12px; margin: 0 0 12px">
      基于感知哈希识别「视觉相似但字节不同」的图片（不同压缩/缩放/水印）。
      <b>默认全库扫描</b>（哈希已缓存时秒级返回，不漏检）；也可改为随机抽样分批扫。
      哈希首次计算后自动缓存；<b>扫描只读缓存、不在请求内补算</b>（否则会超过前端 30 秒
      上限），缓存缺口用「后台补齐哈希缓存」一次补完（按缩略图算，2.7 万张约 1 分钟）。
      仅列出候选，需人工确认后删除。
    </p>

    <!-- 哈希缓存缺口：给出「后台补齐」入口（扫描接口限时补算，补不完的走 worker） -->
    <a-alert v-if="result && result.missing > 0" type="warning" style="margin-bottom: 12px">
      <template #title>哈希缓存未补齐：还差 {{ result.missing }} 张</template>
      本次扫描只覆盖了已缓存的 {{ result.cached_total }} / {{ result.total }} 张，可能漏掉重复对。
      （扫描不在请求内补算——那会让请求超过 30 秒上限；缺口交给下面的后台任务）
      <template v-if="result.unhashable > 0">
        （另有 {{ result.unhashable }} 张文件缺失或格式不支持，补算不动）
      </template>
      <div style="margin-top: 8px">
        <a-space align="center">
          <a-button
            size="small"
            type="primary"
            :loading="backfillBusy"
            :disabled="backfillStatus === 'running' || backfillStatus === 'pending'"
            @click="startBackfill"
          >
            后台补齐哈希缓存
          </a-button>
          <a-checkbox v-model="forceRehash" :disabled="backfillBusy" style="font-size: 12px">
            强制重算全部（哈希口径变更后用，2.7 万张约 1 分钟）
          </a-checkbox>
          <a-progress
            v-if="backfillStatus === 'running' || backfillStatus === 'pending'"
            :percent="backfillProgress / 100"
            size="small"
            style="width: 220px"
          />
          <a-typography-text v-if="backfillMessage" type="secondary" style="font-size: 12px">
            {{ backfillMessage }}
          </a-typography-text>
        </a-space>
      </div>
    </a-alert>

    <!-- 扫描结果汇总 -->
    <!-- 刚提交删除：删除在后台跑，此刻说「未发现近似重复」是假话 -->
    <a-alert v-if="submittedNotice" type="success" style="margin-bottom: 12px">
      {{ submittedNotice }}
    </a-alert>
    <a-alert
      v-else-if="result && result.groups.length === 0"
      type="success"
      style="margin-bottom: 12px"
    >
      <template v-if="result.missing > 0">
        已扫描已缓存的 {{ result.scanned }} 张（哈希缓存还差 {{ result.missing }} 张未补齐），
        暂未发现近似重复；补齐缓存后再扫一次才能覆盖全库
      </template>
      <template v-else-if="result.truncated || scannedLimit > 0">
        已随机扫描 {{ result.scanned }} /
        {{ result.total }} 张，未发现近似重复（仅覆盖本次抽样，可再次扫描或改用全库扫描）
      </template>
      <template v-else> 已全库扫描 {{ result.scanned }} 张，未发现近似重复 </template>
    </a-alert>

    <template v-if="groups.length > 0">
      <p style="color: rgb(var(--warning-6)); margin-bottom: 12px">
        发现 {{ groups.length }} 组近似重复，{{ nearDupScopeLabel(result!, scannedLimit) }}
      </p>

      <a-space align="center" style="margin-bottom: 12px">
        <a-button type="primary" @click="openDupModal"
          >开始逐组对比处理（{{ groups.length }} 组）</a-button
        >
        <a-typography-text type="secondary" style="font-size: 12px">
          弹窗中左右对比每组素材，选择保留哪一张；冗余素材将物理删除（不可恢复）
        </a-typography-text>
      </a-space>
    </template>

    <a-empty v-else-if="!scanning" description="点击「扫描近似重复」开始检测" />
  </a-card>

  <!-- 近似重复逐组对比弹窗：左右并排大图 + 保留决策 -->
  <!-- 单向绑定 :visible（不用 v-model）：点 X 时弹窗不能自己先关掉，要先问「已选的是否提交」 -->
  <a-modal
    :visible="showDupModal"
    title="近似重复素材对比"
    :width="'92%'"
    :modal-style="{ maxWidth: '1200px' }"
    :mask-closable="false"
    :esc-to-close="false"
    :footer="false"
    @cancel="requestExit"
  >
    <!-- 逐组决策视图 -->
    <template v-if="!allDone && currentGroup">
      <div class="dup-step">
        第 {{ dupIndex + 1 }} / {{ dupGroups.length }} 组 · 本组共
        {{ currentGroup?.files.length ?? 0 }} 张近似重复
        <a-tag color="arcoblue" size="small" class="dup-tag-gap">建议保留：{{ keeperHint }}</a-tag>
        <a-tag v-if="currentDecision" color="green" size="small" class="dup-tag-gap">
          本组已选：{{ decisionLabel(currentDecision) }}（可改）
        </a-tag>
        <a-tag v-else-if="currentKeeperId" color="green" size="small" class="dup-tag-gap">
          本组已选：{{ currentKeeperLabel }}（删除其余）
        </a-tag>
        <a-space style="margin-left: 12px">
          <a-button size="mini" :disabled="!canPrev" @click="goPrev">← 上一组</a-button>
          <a-button size="mini" :disabled="!canNext" @click="goNext">下一组 →</a-button>
        </a-space>
      </div>

      <div class="dup-compare">
        <!-- 左图 -->
        <div class="dup-side">
          <div class="dup-side-label">保留左边</div>
          <div class="dup-img-wrap" v-if="leftFile">
            <img :src="getFileUrl(leftFile.file_path)" :alt="`候选素材 ${leftFile.id}`" />
          </div>
          <div class="dup-side-meta" v-if="leftFile">
            ID {{ leftFile.id.slice(0, 8) }} · 上传 {{ fmtTime(leftFile.created_at)
            }}<template v-if="favoriteLabel(leftFile)">{{ favoriteLabel(leftFile) }}</template>
            <br />
            {{ formatSize(leftFile.size_bytes) }}
          </div>
        </div>

        <div class="dup-vs">VS</div>

        <!-- 右图 -->
        <div class="dup-side">
          <div class="dup-side-label">保留右边</div>
          <div class="dup-img-wrap" v-if="rightFile">
            <img :src="getFileUrl(rightFile.file_path)" :alt="`候选素材 ${rightFile.id}`" />
          </div>
          <div class="dup-side-meta" v-if="rightFile">
            ID {{ rightFile.id.slice(0, 8) }} · 上传 {{ fmtTime(rightFile.created_at)
            }}<template v-if="favoriteLabel(rightFile)">{{ favoriteLabel(rightFile) }}</template>
            <br />
            {{ formatSize(rightFile.size_bytes) }}
          </div>
        </div>
      </div>

      <!-- 组内全部素材：不让用户只凭前两张决定其余未展示素材的命运 -->
      <div v-if="currentGroup && currentGroup.files.length > 2" class="dup-all">
        <div class="dup-all-title">
          本组共 {{ currentGroup.files.length }} 张（全部展示，点一张即「保留它、删除其余」）：
        </div>
        <div class="dup-all-strip">
          <button
            v-for="file in currentGroup.files"
            :key="file.id"
            type="button"
            class="dup-all-item"
            :class="{
              'is-keeper': currentKeeperId === file.id,
              'is-suggested': currentGroup.keeper_id === file.id,
            }"
            :title="`保留这张，删除其余 ${currentGroup.files.length - 1} 张`"
            @click="decideKeeper(file.id)"
          >
            <img :src="fileUrl(file)" :alt="`候选素材 ${file.id}`" loading="lazy" />
            <span v-if="currentGroup.keeper_id === file.id" class="dup-all-suggest">建议</span>
            <span v-if="currentKeeperId === file.id" class="dup-all-keeper">保留</span>
            <span v-if="favoriteLabel(file)" class="dup-all-fav">★</span>
            <span class="dup-all-meta">{{ displayFileMeta(file) }}</span>
          </button>
        </div>
      </div>

      <p class="dup-hint">
        组内共有
        {{ currentGroup?.files.length ?? 0 }} 张，本页已全部展示；点缩略图可保留任意一张（其余将
        <strong>永久删除</strong>，文件与记录不可恢复），或点「删除两张」将当前两张一并删除。
        可随时用「← 上一组 / 下一组 →」翻页回看：回到已决定的组再点一次就是改主意，
        提交时按最后一次选择算（决定后 0.3 秒内的连点会被忽略，防手快误删下一组）。
        中途退出会先询问「提交 / 放弃 / 继续」，不再静默提交。
      </p>

      <div class="dup-actions">
        <a-button
          :type="currentKeeperId === leftFile?.id ? 'primary' : 'outline'"
          :disabled="deletingBoth || decisionLocked"
          @click="keepLeft"
          >保留左边</a-button
        >
        <a-button
          :type="currentKeeperId === rightFile?.id ? 'primary' : 'outline'"
          status="warning"
          :disabled="deletingBoth || decisionLocked"
          @click="keepRight"
          >保留右边</a-button
        >
        <a-button type="text" :disabled="deletingBoth || decisionLocked" @click="skipGroup"
          >都保留（跳过本组）{{ currentDecision === 'skip' ? '（已跳过）' : '' }}</a-button
        >
        <a-popconfirm
          :ok-button-props="{ type: 'primary', status: 'danger' }"
          :content="pairDeleteContent"
          @ok="deleteBoth"
        >
          <a-button
            type="outline"
            status="danger"
            :loading="deletingBoth"
            :disabled="!leftFile || !rightFile"
          >
            删除两张
          </a-button>
        </a-popconfirm>
        <a-button type="text" @click="requestExit">退出</a-button>
        <a-button v-if="deleteCount > 0" type="primary" status="danger" @click="allDone = true"
          >去提交（{{ deleteCount }} 个）</a-button
        >
      </div>

      <div class="dup-progress">
        已决定删除 {{ deleteCount }} 个素材 · 已处理 {{ decidedCount }} / {{ dupGroups.length }} 组
        <template v-if="decidedCount < dupGroups.length">（未决定的组会保留原样）</template>
      </div>
    </template>

    <!-- 全部处理完：提交确认视图 -->
    <template v-else-if="allDone">
      <a-result status="info" title="对比完成" class="dup-result">
        <template #subtitle>
          共处理 {{ dupGroups.length }} 组近似重复，决定删除
          <b>{{ deleteCount }}</b> 个冗余素材（物理删除，不可恢复）
        </template>
        <template #extra>
          <a-space class="dup-center-actions">
            <a-button type="text" @click="allDone = false">← 返回修改</a-button>
            <a-popconfirm
              :content="`将物理删除 ${deleteCount} 个素材（文件与记录不可恢复），确定继续？`"
              @ok="requestConfirmSubmit"
            >
              <a-button type="primary" status="danger" :disabled="deleteCount === 0">
                确认提交删除（{{ deleteCount }} 个）
              </a-button>
            </a-popconfirm>
            <a-button @click="requestExit">退出（未提交决定会先询问）</a-button>
          </a-space>
        </template>
      </a-result>
    </template>

    <!-- 候选队列已清空（可能部分素材已通过「删除两张」提交删除） -->
    <template v-else-if="dupGroups.length === 0">
      <a-result status="success" title="暂无更多近似重复素材" class="dup-result">
        <template #subtitle>
          当前候选列表中的近似重复素材已全部处理完毕，可关闭弹窗或重新扫描发现其他素材
        </template>
        <template #extra>
          <a-space class="dup-center-actions">
            <a-button @click="requestExit">完成</a-button>
          </a-space>
        </template>
      </a-result>
    </template>
  </a-modal>

  <!-- 退出确认：有未提交决定时不静默提交 -->
  <a-modal
    v-model:visible="exitConfirmVisible"
    title="还有未提交的删除决定"
    :footer="false"
    :width="460"
    @cancel="exitConfirmVisible = false"
  >
    <p>当前还有 {{ deleteCount }} 个素材被标记为删除。退出前请选择如何处理这些决定：</p>
    <div class="dup-exit-actions">
      <a-button type="primary" status="danger" @click="requestSubmitAndExit">
        提交 {{ deleteCount }} 个删除并退出
      </a-button>
      <a-button @click="discardAndExit">放弃决定并退出</a-button>
      <a-button type="text" @click="exitConfirmVisible = false">继续处理</a-button>
    </div>
  </a-modal>

  <!-- 收藏素材删除保护：待删集合含收藏素材时，提交前再确认一次 -->
  <a-modal
    v-model:visible="favoriteConfirmVisible"
    title="删除包含收藏素材"
    :footer="false"
    :width="480"
    @cancel="cancelFavoriteConfirm"
  >
    <p>
      本次待删除的 {{ deleteCount }} 个素材中，有 <b>{{ favoriteDeleteCount }}</b> 个是已收藏素材（★
      已收藏）。继续提交会 <b>物理删除这些收藏素材</b>（文件与记录不可恢复）。
    </p>
    <p v-if="favoriteFilesToDelete.length > 0" class="dup-favorite-list">
      涉及素材：{{
        favoriteFilesToDelete
          .slice(0, 5)
          .map((file) => file.id.slice(0, 8))
          .join('、')
      }}<template v-if="favoriteFilesToDelete.length > 5">
        等 {{ favoriteFilesToDelete.length }} 个</template
      >
    </p>
    <div class="dup-exit-actions">
      <a-button type="primary" status="danger" @click="confirmFavoriteDelete">
        确认删除（含收藏素材）
      </a-button>
      <a-button @click="cancelFavoriteConfirm">取消，返回继续处理</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
/* 页内重复使用的布局属性收进类，避免模板散落 inline style */
.near-dup-card {
  margin-bottom: 24px;
}
.dup-tag-gap {
  margin-left: 8px;
}
.dup-result {
  margin: 8px 0;
}
.dup-center-actions {
  display: flex;
  justify-content: center;
  width: 100%;
}

/* 逐组步骤提示 */
.dup-step {
  margin-bottom: 12px;
  font-weight: 600;
  color: var(--color-text-1);
}

/* 左右对比布局：等宽两列 + 中间 VS */
.dup-compare {
  display: flex;
  align-items: stretch;
  gap: 12px;
}

.dup-side {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.dup-side-label {
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-2);
  margin-bottom: 8px;
}

/* 图片容器：高度 ≥70vh，图片等比完整显示（不裁切） */
.dup-img-wrap {
  height: 70vh;
  height: 70dvh;
  min-height: 420px;
  background: #111;
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dup-img-wrap img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}

.dup-side-meta {
  margin-top: 8px;
  text-align: center;
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.6;
}

.dup-vs {
  align-self: center;
  font-weight: 800;
  color: var(--color-text-4);
  font-size: 18px;
  padding: 0 2px;
  flex-shrink: 0;
}

.dup-hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.8;
}

.dup-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
}

.dup-progress {
  text-align: center;
  font-size: 12px;
  color: var(--color-text-3);
  margin-top: 12px;
}

/* 组内全部素材条带：避免用户只看到前两张就决定其余未展示素材的命运 */
.dup-all {
  margin-top: 12px;
}
.dup-all-title {
  font-size: 12px;
  color: var(--color-text-2);
  margin-bottom: 6px;
}
.dup-all-strip {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.dup-all-item {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 0 0 112px;
  width: 112px;
  height: 112px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: var(--radius-sm);
  background: var(--sheet-2);
  cursor: pointer;
  overflow: hidden;
}
.dup-all-item img {
  width: 100%;
  height: 86px;
  flex: 0 0 86px;
  object-fit: cover;
  display: block;
}
/* 宽×高 / 标签数 / 评分：帮用户判断保留哪张 */
.dup-all-meta {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 2px;
  font-size: 10px;
  color: var(--ink-2);
  background: var(--sheet);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dup-all-item.is-keeper {
  border-color: var(--danger-6);
}
.dup-all-item.is-suggested {
  box-shadow: inset 0 0 0 2px rgb(var(--warning-6));
}
.dup-all-suggest {
  position: absolute;
  right: 2px;
  top: 2px;
  padding: 0 4px;
  border-radius: 2px;
  background: rgb(var(--warning-6));
  color: #fff;
  font-size: 10px;
  line-height: 16px;
}
.dup-all-keeper {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 22px;
  background: var(--danger-6);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}
.dup-all-fav {
  position: absolute;
  left: 4px;
  top: 2px;
  color: var(--seal);
  font-size: 14px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
}
.dup-exit-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

/* 收藏素材确认：列出涉及素材的 ID 前缀，提醒这些会被物理删除 */
.dup-favorite-list {
  margin: 8px 0 0;
  font-size: 12px;
  color: rgb(var(--danger-6));
  word-break: break-all;
}

@media (max-width: 768px) {
  .dup-compare {
    flex-direction: column;
  }
  .dup-vs {
    align-self: center;
  }
  .dup-img-wrap {
    height: 45dvh;
    min-height: 240px;
  }
  .dup-actions {
    flex-wrap: wrap;
  }
}
</style>
