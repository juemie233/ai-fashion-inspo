<script setup lang="ts">
/** 生成历史表：缩略图 / 已入库 / 筛选 / 分页 / 同参重跑 / 展开入库 / 删除。
 *
 * 从 GenerateView.vue 抽出（视图只做编排）。表格不直接改数据：刷新、重跑、删除、入库
 * 全部 emit 给视图，由它调接口并决定刷新时机。
 *
 * 三件需要留意的设计：
 * - **缩略图不是原图**：候选图 1.6 MB/张，列表用 `?w=256` 的 6 KB 缩略图（后端缓存）；
 * - **展开行才拉详情**：`imported` 逐张判定要算哈希（8 ms/张），只在展开某一行时拉；
 * - **筛选与分页走服务端**：`total` 由接口给，行数不靠本地数组长度推。
 */

import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import type {
  GenerateStatus,
  GenerationCandidate,
  GenerationHistoryQuery,
  GenerationRunBrief,
  GenerationRunStats,
} from '@/api/generate'
import { autoTitleRun, candidateUrl, fetchRun } from '@/api/generate'
import { fetchInspirations } from '@/api/inspirations'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import GenerateImportDialog from '@/components/generate/GenerateImportDialog.vue'
import GenerateHistoryStatsToolbar from '@/components/generate/GenerateHistoryStatsToolbar.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { copyToClipboard } from '@/utils/clipboard'
import { addDaysIso, formatDateTime, shortModelLabel } from '@/utils/format'

const props = defineProps<{
  /** 当前页历史（服务端已排序：created_at desc） */
  history: GenerationRunBrief[]
  /** 筛选后的总条数（服务端给的，用于分页） */
  total: number
  /** 当前页码（1 起） */
  page: number
  /** 每页条数 */
  size: number
  /** 列表刷新中 */
  loading: boolean
  /** 提交中（「同参重跑」的 loading） */
  submitting: boolean
  /** 正在删除的记录 ID（那一行按钮转 loading） */
  deletingId: number | null
  /** 环境状态：读 models_meta 里的档位名与「未审查」标记 */
  status: GenerateStatus | null
  /** 当前筛选条件（受控：由视图持有，表格只回抛变更） */
  filters: GenerationHistoryQuery
  /** 入库中（展开行的「入库选中」按钮 loading） */
  importing?: boolean
  /** 近 30 天统计（历史区顶部数字卡；null = 还没拉到） */
  stats?: GenerationRunStats | null
  /** 候选保留天数（用于「图还剩 N 天」） */
  keepDays?: number
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'retry', id: number): void
  (e: 'remove', id: number): void
  (e: 'page-change', page: number): void
  (e: 'update:filters', filters: GenerationHistoryQuery): void
  /** 入库回抛：rowId=列表行 id；groups=按底层批次分组的文件名（多姿态合并行可能多批） */
  (
    e: 'import-selected-batch',
    rowId: number,
    groups: Array<{ run_id: number; filenames: string[] }>,
  ): void
  /** 切换收藏（收藏的批次不会被过期清理） */
  (e: 'toggle-favorite', runId: number, favorite: boolean): void
  /** 保存命名与备注（clear_* 为显式清空） */
  (
    e: 'save-meta',
    runId: number,
    patch: { title?: string; note?: string; clear_title?: boolean; clear_note?: boolean },
  ): void
  /** 把该批参数带入左侧表单（不直接生成） */
  (e: 'apply-to-form', runId: number): void
  /** 打开该批已入库的素材（素材库按 generation_run_id 筛选） */
  (e: 'open-imported', runId: number): void
  /** 导出当前筛选结果（csv / json） */
  (e: 'export', fmt: 'csv' | 'json'): void
  /** 按参考素材聚合：只看用过这张参考图的批次 */
  (e: 'filter-by-reference', referenceId: string): void
  /** 两批对比：选中两个批次后回抛（左=先选，右=后选） */
  (e: 'compare-runs', ids: [number, number]): void
}>()

/** 生成方式中文标签（kind 由档位决定） */
function kindLabel(kind: string): string {
  return kind === 'i2i' ? '参考图改款' : '文生图'
}

/** 档位 key → 展示名（后端没给档位表时退回 key，不显示空白） */
function modelLabel(key: string): string {
  const meta = props.status?.models_meta?.[key]
  return meta ? shortModelLabel(meta.label) : key || '—'
}

/** 该档位是否为未审查权重（打标用） */
function modelUncensored(key: string): boolean {
  return props.status?.models_meta?.[key]?.uncensored === true
}

/** 该批次是否云端档位（provider=ark）：云端无 seed，重跑=重新生成 */
function isCloudRun(record: GenerationRunBrief): boolean {
  return props.status?.models_meta?.[record.model_key]?.provider === 'ark'
}

/** 操作列重跑文案：云端没有 seed，用「重新生成」避免暗示可复现 */
function retryLabel(record: GenerationRunBrief): string {
  return isCloudRun(record) ? '重新生成' : '同参重跑'
}

/** 候选图 seed 标签：云端每次随机（seed 恒为 0），不要显示误导性的 seed 0 */
function seedText(seed: number): string {
  return seed > 0 ? `seed ${seed}` : '无 seed（每次随机）'
}

/** 从历史参数快照里取数值（老记录可能缺字段，取不到就不展示） */
function paramNumber(record: GenerationRunBrief, key: string): number | null {
  const value = record.params?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function paramText(record: GenerationRunBrief, key: string): string {
  const value = record.params?.[key]
  return typeof value === 'string' ? value : ''
}

/** 尺寸摘要：宽/高都有才显示 */
function dimensions(record: GenerationRunBrief): string {
  const w = paramNumber(record, 'width')
  const h = paramNumber(record, 'height')
  return w && h ? `${w}×${h}` : ''
}

/** 换姿势批次用的姿势 id（来自参数快照；没有则空串） */
function poseId(record: GenerationRunBrief): string {
  const value = record.params?.pose_id
  return typeof value === 'string' ? value : ''
}

/** 参考素材张数（文生图为 0） */
function referenceCount(record: GenerationRunBrief): number {
  const ids = record.params?.reference_ids
  return Array.isArray(ids) ? ids.length : 0
}

/** 复制完整描述：历史列表只存参数，用户常需要把描述搬到别处复用 */
async function copyPrompt(prompt: string): Promise<void> {
  if (!prompt) return
  const ok = await copyToClipboard(prompt)
  if (ok) Message.success('描述已复制')
  else Message.error('复制失败，请手动选择文本')
}

// ── 两批对比选择（最多 2 个；再点已选中的即取消）──

const comparePicked = ref<number[]>([])

function toggleCompare(runId: number): void {
  const idx = comparePicked.value.indexOf(runId)
  if (idx >= 0) {
    comparePicked.value = comparePicked.value.filter((id) => id !== runId)
    return
  }
  // 已选满两个：替换最早选的那个（比「禁止点击」更符合直觉）
  comparePicked.value = [...comparePicked.value.slice(-1), runId]
}

function isComparePicked(runId: number): boolean {
  return comparePicked.value.includes(runId)
}

const canCompare = computed(() => comparePicked.value.length === 2)

function submitCompare(): void {
  const [a, b] = comparePicked.value
  if (a === undefined || b === undefined) {
    Message.warning('请先选中两个批次')
    return
  }
  emit('compare-runs', [a, b])
  comparePicked.value = []
}

/** 显示标题：用户命名优先，否则 prompt 前 40 字（列表列宽有限） */
function displayTitle(record: GenerationRunBrief): string {
  const title = (record.title || '').trim()
  if (title) return title
  const prompt = (record.prompt || '').trim()
  return prompt.length > 40 ? `${prompt.slice(0, 40)}…` : prompt || '（无描述）'
}

/** 保留期文案：收藏批次永不清理；已清理的目录直接说明；其余给剩余天数 */
function retentionText(record: GenerationRunBrief): string {
  if (record.favorite) return '已收藏，不清理'
  if (record.files_missing) return '图已清理'
  const expires = record.expires_at ? new Date(record.expires_at).getTime() : NaN
  if (Number.isNaN(expires)) return props.keepDays ? `保留 ${props.keepDays} 天` : ''
  const leftMs = expires - Date.now()
  if (leftMs <= 0) return '即将清理'
  return `${Math.ceil(leftMs / 86_400_000)} 天后清理`
}

/** 统计卡：入库率/失败率转百分比（保留一位小数，避免 0.0531 这种不好读） */
const importRateText = computed(() =>
  props.stats ? `${Math.round(props.stats.import_rate * 1000) / 10}%` : '—',
)
const failureRateText = computed(() =>
  props.stats ? `${Math.round(props.stats.failure_rate * 1000) / 10}%` : '—',
)

/** 档位分布条：取前 3 个（再多在 200px 里也看不出来） */
const topModels = computed(() => (props.stats?.by_model ?? []).slice(0, 3))
const maxModelRuns = computed(() => Math.max(1, ...topModels.value.map((item) => item.runs)))

// ── 标注编辑（命名 / 备注）──

const metaEditing = ref<GenerationRunBrief | null>(null)
const metaTitle = ref('')
const metaNote = ref('')

function openMetaEditor(record: GenerationRunBrief): void {
  metaEditing.value = record
  metaTitle.value = record.title ?? ''
  metaNote.value = record.note ?? ''
}

/** 自动命名：模型给一个 ≤20 字建议填进输入框（用户仍要自己点保存） */
const autoTitling = ref(false)
async function fillAutoTitle(): Promise<void> {
  const record = metaEditing.value
  if (!record) return
  autoTitling.value = true
  try {
    const result = await autoTitleRun(record.id)
    metaTitle.value = result.title
    Message.success(
      result.source === 'ai' ? '已用模型生成标题，可再修改' : '模型不可用，已用描述前 20 字代替',
    )
  } catch {
    Message.error('自动命名失败，请手动填写')
  } finally {
    autoTitling.value = false
  }
}

function saveMeta(): void {
  const record = metaEditing.value
  if (!record) return
  const title = metaTitle.value.trim()
  const note = metaNote.value.trim()
  emit('save-meta', record.id, {
    title: title || undefined,
    note: note || undefined,
    clear_title: !title,
    clear_note: !note,
  })
  // 这里不关弹窗：等视图确认接口成功后再调 closeMetaEditor；失败时保留输入
}

/** 保存成功后由视图调用：关闭标注弹层（失败时保持打开，避免用户输入丢失） */
function closeMetaEditor(): void {
  metaEditing.value = null
}

// ── 筛选工具栏 ──

/** 档位下拉选项：以探测到的档位表为准（找不到就只给「全部」） */
const modelOptions = computed(() => {
  const meta = props.status?.models_meta ?? {}
  return [
    { label: '全部档位', value: '' },
    ...Object.entries(meta).map(([key, m]) => ({ label: shortModelLabel(m.label), value: key })),
  ]
})

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已完成', value: 'done' },
  { label: '生成中', value: 'running' },
  { label: '排队中', value: 'queued' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
]

/** 关键字输入：本地即时回显，300 ms 防抖后才回抛（避免每敲一个字打一次接口） */
const keyword = ref(String(props.filters.q ?? ''))
let keywordTimer: ReturnType<typeof setTimeout> | null = null

function pushKeyword(): void {
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    keywordTimer = null
    const next = keyword.value.trim() || undefined
    // 与当前筛选一致就不回抛（否则「外部重置 → watch 触发 → 又 emit」会打转）
    if (next === (props.filters.q || undefined)) return
    emit('update:filters', { ...props.filters, q: next })
  }, 300)
}

// 监听输入而不是 @input：Arco 的 a-input 不保证透出原生 input 事件
watch(keyword, () => pushKeyword())

onBeforeUnmount(() => {
  if (keywordTimer) clearTimeout(keywordTimer)
})

// 外部重置筛选（例如点「清空」）时同步输入框
watch(
  () => props.filters.q,
  (value) => {
    const next = String(value ?? '')
    if (next !== keyword.value) keyword.value = next
  },
)

function patchFilters(patch: Partial<GenerationHistoryQuery>): void {
  emit('update:filters', { ...props.filters, ...patch })
}

/** 日期范围选择器的显示值：`until` 后端存的是「次日零点」，展示时要换回「含当天」 */
const dateRange = computed<[string, string] | undefined>(() => {
  const since = String(props.filters.since ?? '')
  const untilExclusive = String(props.filters.until ?? '')
  const untilInclusive = untilExclusive ? addDaysIso(untilExclusive, -1) : ''
  if (!since && !untilInclusive) return undefined
  return [since, untilInclusive]
})

/** 选日期：结束日 +1 天作为开区间上界（用户选 10-26 就要包含 26 号全天） */
function onDateRangeChange(value: unknown): void {
  const range = Array.isArray(value) ? value : []
  const since = String(range[0] ?? '').slice(0, 10)
  const end = String(range[1] ?? '').slice(0, 10)
  patchFilters({
    since: since || undefined,
    until: end ? addDaysIso(end, 1) || undefined : undefined,
  })
}

const filtered = computed(() =>
  Boolean(
    props.filters.model_key ||
    props.filters.status ||
    props.filters.has_imported ||
    props.filters.q ||
    props.filters.since ||
    props.filters.until ||
    props.filters.reference_id ||
    props.filters.favorite,
  ),
)

// ── 展开行：按需拉详情（含逐张 imported）──

interface RunDetailState {
  items: GenerationCandidate[]
  loading: boolean
  error: string | null
}

const details = reactive<Record<number, RunDetailState>>({})
/** 展开行里勾选的候选选择键（行 id → select_key 集合） */
const pickedByRun = reactive<Record<number, string[]>>({})

/** 列表行包含的底层批次 id（多姿态合并行 >1；单批次 = [id]） */
function rowRunIds(record: GenerationRunBrief): number[] {
  return record.run_ids?.length ? record.run_ids : [record.id]
}

/** 是否为多姿态合并行 */
function isGroupRow(record: GenerationRunBrief): boolean {
  return rowRunIds(record).length > 1
}

/** 候选选择键：同一次多姿态提交里各批文件名相同，必须带 run_id 区分 */
function candidateKey(item: GenerationCandidate): string {
  return item.select_key ?? `${item.run_id ?? 0}:${item.filename}`
}

/** 拉取一行对应的全部候选图（多姿态行逐批拉详情后合并） */
async function fetchRowItems(record: GenerationRunBrief): Promise<GenerationCandidate[]> {
  const runIds = rowRunIds(record)
  const settled = await Promise.allSettled(runIds.map((id) => fetchRun(id)))
  const merged: GenerationCandidate[] = []
  settled.forEach((result, index) => {
    if (result.status !== 'fulfilled') return
    const out = result.value
    // 测试/旧接口可能没回 run.id：退回发起请求时的批次 id
    const batchRunId = out.run.id ?? runIds[index]
    for (const item of out.items ?? []) {
      merged.push({
        ...item,
        run_id: batchRunId,
        select_key: `${batchRunId}:${item.filename}`,
      })
    }
  })
  if (merged.length === 0) throw new Error('候选图读取失败')
  return merged
}

async function loadDetail(record: GenerationRunBrief): Promise<void> {
  const rowId = record.id
  if (!details[rowId]) details[rowId] = { items: [], loading: false, error: null }
  // ⚠ 必须从 reactive 容器里读回 **proxy** 再改：直接改赋进去的原始对象不会触发更新
  // （踩过：展开行一直显示「这里没有候选图」还转圈）
  const state = details[rowId]
  if (state.loading) return
  state.loading = true
  state.error = null
  try {
    state.items = await fetchRowItems(record)
  } catch {
    // 详情拉不到不影响看列表：展开行给一句提示即可
    state.error = '候选图读取失败（批次可能已被清理）'
    state.items = []
  } finally {
    state.loading = false
  }
}

/** Arco 的 @expand 签名是 (rowKey: string|number, record: TableData)：这里做一次窄化 */
function onExpandRow(_rowKey: string | number, record: Record<string, unknown>): void {
  onExpand(record as unknown as GenerationRunBrief, true)
  void loadReferenceThumbs(record as unknown as GenerationRunBrief)
}

/** 参考素材缩略图（按行缓存）：用于「按参考素材聚合」的入口 */
const refThumbs = reactive<Record<number, Array<{ id: string; url: string }>>>({})

async function loadReferenceThumbs(record: GenerationRunBrief): Promise<void> {
  if (refThumbs[record.id]) return
  const ids = Array.isArray(record.params?.reference_ids)
    ? (record.params?.reference_ids as unknown[]).map(String).filter(Boolean)
    : []
  if (!ids.length) return
  refThumbs[record.id] = [] // 占位，避免重复请求
  try {
    const data = await fetchInspirations({ ids: ids.join(','), size: ids.length })
    const byId = new Map(data.items.map((item) => [item.id, item]))
    refThumbs[record.id] = ids
      .map((id) => byId.get(id))
      .filter((item): item is NonNullable<typeof item> => Boolean(item))
      .map((item) => ({
        id: item.id,
        url: item.thumbnail_path
          ? `/api/files/${item.thumbnail_path.split('/').map(encodeURIComponent).join('/')}`
          : `/api/files/${item.file_path.split('/').map(encodeURIComponent).join('/')}`,
      }))
  } catch {
    refThumbs[record.id] = []
  }
}

function onExpand(record: GenerationRunBrief, expanded: boolean): void {
  if (expanded && !details[record.id]?.items.length) void loadDetail(record)
}

/** 勾选/取消某张候选（已入库的不可选：后端会按哈希去重，前端不该给这个选项） */
function togglePick(rowId: number, selectKey: string): void {
  const list = pickedByRun[rowId] ?? []
  pickedByRun[rowId] = list.includes(selectKey)
    ? list.filter((key) => key !== selectKey)
    : [...list, selectKey]
}

function pickCount(rowId: number): number {
  return (pickedByRun[rowId] ?? []).length
}

/** 把选中的候选按底层批次分组（多姿态合并行会一次入库多个批次的图） */
function pickedGroups(rowId: number): Array<{ run_id: number; filenames: string[] }> {
  const picked = new Set(pickedByRun[rowId] ?? [])
  const groups = new Map<number, string[]>()
  for (const item of details[rowId]?.items ?? []) {
    if (!picked.has(candidateKey(item)) || item.run_id == null) continue
    const filenames = groups.get(item.run_id) ?? []
    filenames.push(item.filename)
    groups.set(item.run_id, filenames)
  }
  return [...groups].map(([runId, filenames]) => ({ run_id: runId, filenames }))
}

function submitImport(rowId: number): void {
  const groups = pickedGroups(rowId)
  if (!groups.length) {
    Message.warning('请先勾选要入库的候选图')
    return
  }
  emit('import-selected-batch', rowId, groups)
}

/** 入库成功后由视图调它清空勾选（并刷新行数据） */
function resetPicked(rowId: number): void {
  pickedByRun[rowId] = []
  details[rowId] = { items: [], loading: false, error: null }
  // 重新拉一次详情：否则仍展开的行会停在「这里没有候选图」的空态（图其实还在）
  const record = props.history.find((item) => item.id === rowId)
  if (record) void loadDetail(record)
}

// ── 操作列「入库」：默认全部入库；多张时弹窗确认（默认全选）──

/** 正在读取候选图的批次 id（那一行的「入库」按钮转 loading） */
const preparingImportId = ref<number | null>(null)
const importDialogVisible = ref(false)
const importDialogRunId = ref<number | null>(null)
const importDialogItems = ref<GenerationCandidate[]>([])

/** 终态 done 且候选目录还在，才给「入库」入口（运行中/已清理没有可入库的图） */
function canImport(record: GenerationRunBrief): boolean {
  return record.status === 'done' && !record.files_missing
}

/**
 * 点「入库」：先拉详情拿到未入库候选。
 * - 只有 1 张可入库 → 直接提交（用户要求的「默认全部入库」）；
 * - 多张 → 打开弹窗并默认全选，由用户勾选后再提交。
 * 两种情况都回抛 `import-selected`，由视图统一调确认入库接口并刷新。
 */
async function openImportDialog(record: GenerationRunBrief): Promise<void> {
  if (preparingImportId.value !== null) return
  preparingImportId.value = record.id
  try {
    const items = await fetchRowItems(record)
    const selectable = items.filter((item) => !item.imported)
    if (!selectable.length) {
      Message.info(
        isGroupRow(record) ? '本次提交的候选图都已入库或已不存在' : '这批候选图都已入库或已不存在',
      )
      return
    }
    if (selectable.length === 1) {
      emit('import-selected-batch', record.id, [
        { run_id: selectable[0].run_id as number, filenames: [selectable[0].filename] },
      ])
      return
    }
    importDialogRunId.value = record.id
    importDialogItems.value = items
    importDialogVisible.value = true
  } catch (e) {
    Message.error(getApiErrorMessage(e, '读取候选图失败'))
  } finally {
    preparingImportId.value = null
  }
}

function onImportDialogConfirm(files: Array<{ run_id: number; filename: string }>): void {
  if (importDialogRunId.value === null || !files.length) return
  const groups = new Map<number, string[]>()
  for (const file of files) {
    const filenames = groups.get(file.run_id) ?? []
    filenames.push(file.filename)
    groups.set(file.run_id, filenames)
  }
  emit(
    'import-selected-batch',
    importDialogRunId.value,
    [...groups].map(([runId, filenames]) => ({ run_id: runId, filenames })),
  )
}

/** 入库后视图刷新了历史：关掉弹窗（避免对着旧候选再点一次） */
watch(
  () => props.history,
  () => {
    if (importDialogVisible.value) importDialogVisible.value = false
  },
)

// 暴露给视图（入库后清勾选）与测试（日期换算的边界：Arco 弹层在 jsdom 里不稳定）
defineExpose({ resetPicked, onDateRangeChange, closeMetaEditor })
</script>

<template>
  <GenerateHistoryStatsToolbar
    :stats="stats || null"
    :import-rate-text="importRateText"
    :failure-rate-text="failureRateText"
    :top-models="topModels"
    :max-model-runs="maxModelRuns"
    :model-label="modelLabel"
    :filters="filters"
    :model-options="modelOptions"
    :status-options="statusOptions"
    :date-range="dateRange"
    :keyword="keyword"
    :filtered="filtered"
    :can-compare="canCompare"
    :compare-count="comparePicked.length"
    :total="total"
    :loading="loading"
    @refresh="emit('refresh')"
    @update:filters="emit('update:filters', $event)"
    @date-change="onDateRangeChange"
    @update:keyword="keyword = $event"
    @export="(fmt: 'csv' | 'json') => emit('export', fmt)"
    @compare="submitCompare"
  />

  <div v-if="!history.length" class="gen-empty">
    {{ filtered ? '没有符合筛选条件的生成记录。' : '还没有生成记录。' }}
  </div>
  <a-table
    v-else
    :data="history"
    :pagination="false"
    row-key="id"
    size="small"
    :scroll="{ x: 1480 }"
    :expandable="{ width: 40 }"
    @expand="onExpandRow"
  >
    <template #columns>
      <a-table-column title="#" data-index="id" :width="64" />
      <!-- 收藏：星标即「这批别清掉」。收藏批次不参与过期清理 -->
      <a-table-column title="收藏" :width="56">
        <template #cell="{ record }">
          <a-button
            size="mini"
            type="text"
            :title="record.favorite ? '取消收藏（之后会按保留期清理）' : '收藏（图不会被自动清理）'"
            class="gh-star"
            :class="{ 'is-on': record.favorite }"
            @click="emit('toggle-favorite', record.id, !record.favorite)"
          >
            {{ record.favorite ? '★' : '☆' }}
          </a-button>
        </template>
      </a-table-column>
      <!-- 缩略图：单批最多 4 张；多姿态合并行把各批缩略图拼在一起（展开看全部） -->
      <a-table-column title="图" :width="200">
        <template #cell="{ record }">
          <div v-if="record.candidates?.length" class="gh-thumbs">
            <!-- 悬停自动放大预览（大图走 ?w=1024 缩略图，不拉原图） -->
            <HoverImagePreview
              v-for="thumb in record.candidates"
              :key="`${thumb.run_id ?? record.id}:${thumb.filename}`"
              :src="thumb.thumb_url"
              :large-src="candidateUrl(thumb.run_id ?? record.id, thumb.filename, 1024)"
              :alt="thumb.filename"
              img-class="gh-thumb"
              :delay="120"
              inline
            />
          </div>
          <span v-else-if="record.files_missing" class="gh-missing">图已清理</span>
          <span v-else class="gh-missing">—</span>
          <a-tag v-if="isGroupRow(record)" size="small" class="gh-group-tag">
            多姿态 {{ rowRunIds(record).length }}
          </a-tag>
          <span
            v-if="(record.candidate_total ?? 0) > (record.candidates?.length ?? 0)"
            class="gh-more"
          >
            共 {{ record.candidate_total }} 张
          </span>
        </template>
      </a-table-column>
      <!-- 命名优先、否则描述前 40 字；完整描述在展开行 -->
      <a-table-column title="描述 / 命名" :width="340">
        <template #cell="{ record }">
          <div class="gh-title" :title="record.prompt || ''">{{ displayTitle(record) }}</div>
          <div v-if="record.note" class="gh-note" :title="record.note">备注：{{ record.note }}</div>
        </template>
      </a-table-column>
      <a-table-column title="模型" :width="190">
        <template #cell="{ record }">
          <span class="gen-model-cell">
            {{ modelLabel(record.model_key) }}
            <a-tag v-if="modelUncensored(record.model_key)" color="red" size="small">
              未审查
            </a-tag>
          </span>
        </template>
      </a-table-column>
      <a-table-column title="状态" :width="150">
        <template #cell="{ record }">
          <!-- 状态文案与配色统一走 common/StatusTag（单一来源 utils/taskLabel.ts） -->
          <StatusTag :status="record.status" />
          <span class="gen-status-count">{{ record.done }}/{{ record.total }}</span>
          <a-tag v-if="isGroupRow(record)" size="small" class="gh-group-tag">
            多姿态 {{ rowRunIds(record).length }}
          </a-tag>
          <a-tag
            v-if="record.imported_count"
            size="small"
            color="green"
            class="gh-imported"
            :title="`查看这批入库的 ${record.imported_count} 张素材`"
            @click="emit('open-imported', record.id)"
          >
            已入库 {{ record.imported_count }}
          </a-tag>
        </template>
      </a-table-column>
      <a-table-column title="保留" :width="110">
        <template #cell="{ record }">
          <span class="gh-retention" :class="{ 'is-gone': record.files_missing }">
            {{ retentionText(record) }}
          </span>
        </template>
      </a-table-column>
      <a-table-column title="时间" :width="150">
        <template #cell="{ record }">
          <span style="white-space: nowrap">{{ formatDateTime(record.created_at) }}</span>
        </template>
      </a-table-column>
      <a-table-column title="操作" :width="380" fixed="right">
        <template #cell="{ record }">
          <a-button
            v-if="!isGroupRow(record)"
            size="mini"
            :loading="submitting"
            :title="
              isCloudRun(record)
                ? '云端无 seed：按原素材/姿势重新生成一个批次'
                : '同参数新建批次（可复现或生成变体）'
            "
            @click="emit('retry', record.id)"
          >
            {{ retryLabel(record) }}
          </a-button>
          <!-- 带入表单：把参数填回左侧面板，可先改 seed/参考图再生成（与「同参重跑」互补） -->
          <a-button size="mini" style="margin-left: 6px" @click="emit('apply-to-form', record.id)">
            带入表单
          </a-button>
          <a-button size="mini" style="margin-left: 6px" @click="openMetaEditor(record)">
            标注
          </a-button>
          <a-button
            size="mini"
            style="margin-left: 6px"
            :type="isComparePicked(record.id) ? 'primary' : 'secondary'"
            @click="toggleCompare(record.id)"
          >
            {{ isComparePicked(record.id) ? '已选对比' : '对比' }}
          </a-button>
          <!-- 入库：默认全部入库；多张时弹窗默认全选让用户挑（已入库的不可重复选） -->
          <a-button
            v-if="canImport(record)"
            size="mini"
            type="primary"
            style="margin-left: 6px"
            :loading="preparingImportId === record.id"
            title="默认全部入库；有多张候选时弹窗选择"
            @click="openImportDialog(record)"
          >
            入库
          </a-button>
          <!-- 只对已结束的批次给删除：运行中的批次后端会拒绝（400），按钮不显示更清楚 -->
          <a-popconfirm
            v-if="['done', 'failed', 'cancelled'].includes(record.status)"
            :content="
              isGroupRow(record)
                ? `删除本次多姿态的 ${rowRunIds(record).length} 个批次及其候选图？`
                : '删除这条生成记录及其候选图？（只清理本批次的候选文件）'
            "
            ok-text="删除"
            @ok="emit('remove', record.id)"
          >
            <a-button
              size="mini"
              status="danger"
              style="margin-left: 6px"
              :loading="deletingId === record.id"
            >
              删除
            </a-button>
          </a-popconfirm>
        </template>
      </a-table-column>
    </template>

    <!-- 展开行：完整描述 + 负面词 + 生成参数 + 失败原因（列表列放不下的都在这里） -->
    <template #expand-row="{ record }">
      <div class="ghd" data-test="history-detail">
        <div class="ghd-row">
          <span class="ghd-label">完整描述</span>
          <div class="ghd-text">{{ record.prompt || '—' }}</div>
        </div>
        <div v-if="record.negative" class="ghd-row">
          <span class="ghd-label">负面提示词</span>
          <div class="ghd-text">{{ record.negative }}</div>
        </div>
        <div class="ghd-row">
          <span class="ghd-label">生成参数</span>
          <div class="ghd-params">
            <span class="ghd-chip">{{ kindLabel(record.kind) }}</span>
            <span v-if="dimensions(record)" class="ghd-chip">尺寸 {{ dimensions(record) }}</span>
            <span v-if="paramNumber(record, 'steps') !== null" class="ghd-chip">
              步数 {{ paramNumber(record, 'steps') }}
            </span>
            <span v-if="paramNumber(record, 'cfg') !== null" class="ghd-chip">
              CFG {{ paramNumber(record, 'cfg') }}
            </span>
            <span v-if="paramText(record, 'sampler')" class="ghd-chip">
              采样器 {{ paramText(record, 'sampler') }}
            </span>
            <span v-if="paramText(record, 'scheduler')" class="ghd-chip">
              调度器 {{ paramText(record, 'scheduler') }}
            </span>
            <span v-if="paramNumber(record, 'seed_base') !== null" class="ghd-chip">
              seed {{ paramNumber(record, 'seed_base') }}
            </span>
            <span class="ghd-chip">张数 {{ record.total }}</span>
            <span v-if="referenceCount(record)" class="ghd-chip">
              参考素材 {{ referenceCount(record) }} 张
            </span>
            <!-- 换姿势：显示姿势 id（含体系/编号/角度），便于在历史里区分几百个批次 -->
            <span v-if="poseId(record)" class="ghd-chip" :title="`姿势 id：${poseId(record)}`">
              姿势 {{ poseId(record) }}
            </span>
          </div>
        </div>
        <div v-if="record.error" class="ghd-row">
          <span class="ghd-label">失败原因</span>
          <div class="ghd-text ghd-error">{{ record.error }}</div>
        </div>
        <!-- 参考素材（C4）：点一张 = 只看用过这张参考图的批次 -->
        <div v-if="(refThumbs[record.id] || []).length" class="ghd-row">
          <span class="ghd-label">参考素材</span>
          <div class="ghd-refs">
            <button
              v-for="refItem in refThumbs[record.id]"
              :key="refItem.id"
              type="button"
              class="ghd-ref"
              :title="`只看用过这张参考图的批次（${refItem.id}）`"
              @click="emit('filter-by-reference', refItem.id)"
            >
              <img :src="refItem.url" alt="" loading="lazy" />
            </button>
            <span class="gen-history-hint">点参考图 = 只看用过它的批次</span>
          </div>
        </div>
        <!-- 全部候选图（展开才拉详情：逐张 imported 要算哈希，列表不做） -->
        <div class="ghd-row">
          <span class="ghd-label">候选图</span>
          <div class="ghd-candidates">
            <a-spin :loading="details[record.id]?.loading" style="display: block">
              <div v-if="details[record.id]?.error" class="ghd-error">
                {{ details[record.id]?.error }}
              </div>
              <div v-else-if="!(details[record.id]?.items || []).length" class="gen-history-hint">
                这里没有候选图（可能已入库并清理，或还没生成完）
              </div>
              <div v-else class="ghd-cand-grid">
                <label
                  v-for="item in details[record.id]?.items || []"
                  :key="candidateKey(item)"
                  class="ghd-cand"
                  :class="{ 'is-imported': item.imported }"
                >
                  <!-- 悬停自动放大预览（候选图原图较大，预览用 ?w=1024 缩略图） -->
                  <HoverImagePreview
                    :src="item.url"
                    :large-src="candidateUrl(item.run_id ?? record.id, item.filename, 1024)"
                    :alt="item.filename"
                    :delay="120"
                  />
                  <span class="ghd-cand-seed">{{ seedText(item.seed) }}</span>
                  <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
                  <a-checkbox
                    v-else
                    :model-value="(pickedByRun[record.id] || []).includes(candidateKey(item))"
                    @change="togglePick(record.id, candidateKey(item))"
                  />
                </label>
              </div>
            </a-spin>
            <div v-if="(details[record.id]?.items || []).length" class="ghd-cand-actions">
              <span class="gen-history-hint">
                已勾选 {{ pickCount(record.id) }} 张（已入库的不可重复选<template
                  v-if="isGroupRow(record)"
                  >；多姿态合并行会按批次分别入库</template
                >）
              </span>
              <a-button
                size="mini"
                type="primary"
                :loading="importing"
                :disabled="pickCount(record.id) === 0"
                @click="submitImport(record.id)"
              >
                入库选中
              </a-button>
            </div>
          </div>
        </div>
        <div class="ghd-meta">
          <span>模型 {{ modelLabel(record.model_key) }}</span>
          <span>工作流 {{ record.workflow_name || '—' }}</span>
          <span>创建 {{ formatDateTime(record.created_at) }}</span>
          <span>最后更新 {{ formatDateTime(record.updated_at) }}</span>
          <a-button size="mini" type="text" @click="copyPrompt(record.prompt)">复制描述</a-button>
        </div>
      </div>
    </template>
  </a-table>

  <!-- 服务端分页：total 来自接口（筛选后的总数），不是本地数组长度 -->
  <a-pagination
    v-if="total > size"
    class="gh-pagination"
    :current="page"
    :page-size="size"
    :total="total"
    size="small"
    @change="(p: number) => emit('page-change', p)"
  />

  <!-- 命名 / 备注编辑：清空是显式的（后端 PATCH 里 null 表示「本次不改」） -->
  <a-modal
    :visible="metaEditing !== null"
    title="标注这一批"
    :width="520"
    :footer="false"
    unmount-on-close
    @cancel="metaEditing = null"
  >
    <div class="gh-meta-form">
      <div class="gen-label gh-meta-label" style="margin-top: 0">
        命名（留空则显示描述前 40 字）
        <a-button size="mini" type="text" :loading="autoTitling" @click="fillAutoTitle">
          AI 起名
        </a-button>
      </div>
      <a-input
        v-model="metaTitle"
        placeholder="例：通勤白衬衫-袖子加长版"
        :max-length="64"
        show-word-limit
      />
      <div class="gen-label">备注</div>
      <a-textarea
        v-model="metaNote"
        placeholder="例：袖子稍长，下次把「长袖」写进描述再看一版"
        :auto-size="{ minRows: 3, maxRows: 6 }"
        :max-length="2000"
        show-word-limit
      />
      <div class="gh-meta-actions">
        <a-button size="small" @click="metaEditing = null">取消</a-button>
        <a-button size="small" type="primary" @click="saveMeta">保存</a-button>
      </div>
    </div>
  </a-modal>

  <!-- 入库候选选择弹窗（多张时默认全选；图可悬停放大） -->
  <generate-import-dialog
    v-model:visible="importDialogVisible"
    :run-id="importDialogRunId"
    :candidates="importDialogItems"
    @confirm="onImportDialogConfirm"
  />
</template>

<style scoped src="./GenerateHistoryTable.css"></style>
