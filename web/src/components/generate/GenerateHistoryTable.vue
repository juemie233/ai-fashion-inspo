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
import { autoTitleRun, fetchRun } from '@/api/generate'
import { fetchInspirations } from '@/api/inspirations'
import StatusTag from '@/components/common/StatusTag.vue'
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
  (e: 'import-selected', runId: number, filenames: string[]): void
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
/** 展开行里勾选的候选文件名（runId → 文件名集合） */
const pickedByRun = reactive<Record<number, string[]>>({})

async function loadDetail(runId: number): Promise<void> {
  if (!details[runId]) details[runId] = { items: [], loading: false, error: null }
  // ⚠ 必须从 reactive 容器里读回 **proxy** 再改：直接改赋进去的原始对象不会触发更新
  // （踩过：展开行一直显示「这里没有候选图」还转圈）
  const state = details[runId]
  if (state.loading) return
  state.loading = true
  state.error = null
  try {
    const out = await fetchRun(runId)
    state.items = out.items ?? []
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
  if (expanded && !details[record.id]?.items.length) void loadDetail(record.id)
}

/** 勾选/取消某张候选（已入库的不可选：后端会按哈希去重，前端不该给这个选项） */
function togglePick(runId: number, filename: string): void {
  const list = pickedByRun[runId] ?? []
  pickedByRun[runId] = list.includes(filename)
    ? list.filter((name) => name !== filename)
    : [...list, filename]
}

function pickCount(runId: number): number {
  return (pickedByRun[runId] ?? []).length
}

function submitImport(runId: number): void {
  const filenames = pickedByRun[runId] ?? []
  if (!filenames.length) {
    Message.warning('请先勾选要入库的候选图')
    return
  }
  emit('import-selected', runId, filenames)
}

/** 入库成功后由视图调它清空勾选（并刷新行数据） */
function resetPicked(runId: number): void {
  pickedByRun[runId] = []
  details[runId] = { items: [], loading: false, error: null }
  // 重新拉一次详情：否则仍展开的行会停在「这里没有候选图」的空态（图其实还在）
  void loadDetail(runId)
}

// 暴露给视图（入库后清勾选）与测试（日期换算的边界：Arco 弹层在 jsdom 里不稳定）
defineExpose({ resetPicked, onDateRangeChange, closeMetaEditor })
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成历史</span>
    <span class="gen-history-hint">点行首箭头看完整描述、参数与全部候选图</span>
    <a-button size="mini" :loading="loading" @click="emit('refresh')">刷新</a-button>
  </div>

  <!-- 统计卡（近 30 天）：入库率按「张」算，耗时含排队等待 -->
  <div v-if="stats" class="gh-stats">
    <div class="gh-stat">
      <b>{{ stats.runs }}</b
      ><span>批次</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.images }}</b
      ><span>出图</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.imported_images }}</b
      ><span>入库（{{ importRateText }}）</span>
    </div>
    <div class="gh-stat">
      <b>{{ stats.avg_duration_s }}s</b><span>均耗时</span>
    </div>
    <div class="gh-stat">
      <b>{{ failureRateText }}</b
      ><span>失败率</span>
    </div>
    <div v-if="topModels.length" class="gh-stat-models">
      <div v-for="item in topModels" :key="item.model_key" class="gh-stat-model">
        <span class="gh-stat-model-name">{{ modelLabel(item.model_key) }}</span>
        <span class="gh-stat-model-bar">
          <i :style="{ width: `${Math.round((item.runs / maxModelRuns) * 100)}%` }" />
        </span>
        <span class="gh-stat-model-num">{{ item.runs }} 批</span>
      </div>
    </div>
  </div>

  <!-- 筛选工具栏：全部走后端参数（total 与 items 同口径，分页才不会飘） -->
  <div class="gh-toolbar">
    <a-select
      :model-value="filters.model_key || ''"
      :options="modelOptions"
      size="mini"
      style="width: 170px"
      @update:model-value="patchFilters({ model_key: String($event) || undefined })"
    />
    <a-select
      :model-value="filters.status || ''"
      :options="statusOptions"
      size="mini"
      style="width: 120px"
      @update:model-value="patchFilters({ status: String($event) || undefined })"
    />
    <a-checkbox
      :model-value="Boolean(filters.has_imported)"
      @update:model-value="patchFilters({ has_imported: Boolean($event) || undefined })"
    >
      只看已入库
    </a-checkbox>
    <a-range-picker
      :model-value="dateRange"
      size="mini"
      style="width: 216px"
      value-format="YYYY-MM-DD"
      @change="onDateRangeChange"
    />
    <a-input
      v-model="keyword"
      size="mini"
      placeholder="搜描述 / 负面词"
      allow-clear
      style="width: 180px"
    />
    <a-button v-if="filtered" size="mini" type="text" @click="emit('update:filters', {})">
      清空筛选
    </a-button>
    <!-- 参考素材筛选的可见反馈（从展开行点参考图进来） -->
    <a-tag
      v-if="filters.reference_id"
      size="small"
      closable
      @close="patchFilters({ reference_id: undefined })"
    >
      只看这张参考图（{{ String(filters.reference_id).slice(0, 8) }}…）
    </a-tag>
    <!-- 导出当前筛选结果（C2）：走接口拿真文件名，且带上筛选条件 -->
    <a-dropdown>
      <a-button size="mini" style="margin-left: auto">导出</a-button>
      <template #content>
        <a-doption @click="emit('export', 'csv')">导出 CSV（Excel 可直接打开）</a-doption>
        <a-doption @click="emit('export', 'json')">导出 JSON</a-doption>
      </template>
    </a-dropdown>
    <a-button size="mini" type="primary" :disabled="!canCompare" @click="submitCompare">
      对比这两批{{ comparePicked.length ? `（${comparePicked.length}/2）` : '' }}
    </a-button>
    <span class="gen-history-hint">共 {{ total }} 条</span>
  </div>

  <div v-if="!history.length" class="gen-empty">
    {{ filtered ? '没有符合筛选条件的生成记录。' : '还没有生成记录。' }}
  </div>
  <a-table
    v-else
    :data="history"
    :pagination="false"
    row-key="id"
    size="small"
    :scroll="{ x: 1420 }"
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
      <!-- 缩略图：最多 4 张（后端只给前 4 张，列表不是候选区）；目录已清显示占位 -->
      <a-table-column title="图" :width="200">
        <template #cell="{ record }">
          <div v-if="record.candidates?.length" class="gh-thumbs">
            <img
              v-for="thumb in record.candidates"
              :key="thumb.filename"
              :src="thumb.thumb_url"
              :alt="thumb.filename"
              loading="lazy"
              class="gh-thumb"
            />
          </div>
          <span v-else-if="record.files_missing" class="gh-missing">图已清理</span>
          <span v-else class="gh-missing">—</span>
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
      <a-table-column title="操作" :width="320">
        <template #cell="{ record }">
          <a-button size="mini" :loading="submitting" @click="emit('retry', record.id)">
            同参重跑
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
          <!-- 只对已结束的批次给删除：运行中的批次后端会拒绝（400），按钮不显示更清楚 -->
          <a-popconfirm
            v-if="['done', 'failed', 'cancelled'].includes(record.status)"
            content="删除这条生成记录及其候选图？（只清理本批次的候选文件）"
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
                  :key="item.filename"
                  class="ghd-cand"
                  :class="{ 'is-imported': item.imported }"
                >
                  <img :src="item.url" :alt="item.filename" loading="lazy" />
                  <span class="ghd-cand-seed">seed {{ item.seed }}</span>
                  <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
                  <a-checkbox
                    v-else
                    :model-value="(pickedByRun[record.id] || []).includes(item.filename)"
                    @change="togglePick(record.id, item.filename)"
                  />
                </label>
              </div>
            </a-spin>
            <div v-if="(details[record.id]?.items || []).length" class="ghd-cand-actions">
              <span class="gen-history-hint">
                已勾选 {{ pickCount(record.id) }} 张（已入库的不可重复选）
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
</template>

<style scoped>
/* 下面 .gen-label / .gen-result-head / .gen-hint 在视图里另有一份：
   生成结果卡与参数面板也要用，scoped 样式不能跨组件继承，故各自保留。 */
.gen-label {
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.gen-result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gen-history-hint {
  flex: 1;
  margin-left: 8px;
  color: #8a8f99;
  font-size: 12px;
}
.gen-model-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.gen-status-count {
  margin-left: 6px;
  color: #8a8f99;
  font-size: 12px;
  white-space: nowrap;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
/* 展开行：描述按原样保留换行（提示词里常有分句换行） */
.ghd {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 12px 10px;
}
.ghd-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.ghd-label {
  flex: 0 0 76px;
  color: #86909c;
  font-size: 12px;
  line-height: 20px;
}
.ghd-text {
  flex: 1;
  min-width: 0;
  color: #1d2129;
  font-size: 13px;
  line-height: 20px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ghd-error {
  color: #f53f3f;
}
.ghd-params {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
}
.ghd-chip {
  padding: 0 6px;
  border-radius: 3px;
  background: #f2f3f5;
  color: #4e5969;
  font-size: 12px;
  line-height: 20px;
}
.ghd-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: #86909c;
  font-size: 12px;
}

/* ── P1：统计卡 / 收藏星标 / 标题与备注 / 保留期 ── */
.gh-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 18px;
  margin: 8px 0 10px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #f7f8fa;
}

.gh-stat {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  color: #86909c;
}

.gh-stat b {
  font-size: 16px;
  color: #1d2129;
}

.gh-stat-models {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 240px;
}

.gh-stat-model {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #4e5969;
}

.gh-stat-model-name {
  width: 110px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.gh-stat-model-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #e5e6eb;
  overflow: hidden;
}

.gh-stat-model-bar i {
  display: block;
  height: 100%;
  background: rgb(var(--primary-5));
}

.gh-star {
  color: #c9cdd4;
  font-size: 15px;
  line-height: 1;
}

.gh-star.is-on {
  color: #ff7d00;
}

.gh-title {
  color: #1d2129;
  font-size: 13px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.gh-note {
  margin-top: 2px;
  color: #86909c;
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.gh-retention {
  color: #86909c;
  font-size: 12px;
  white-space: nowrap;
}

.gh-retention.is-gone {
  color: #f53f3f;
}

/* 参考素材：小方图，点击 = 按这张参考图筛选 */
.ghd-refs {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.ghd-ref {
  padding: 0;
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  overflow: hidden;
  background: none;
  cursor: pointer;
  line-height: 0;
}

.ghd-ref:hover {
  border-color: rgb(var(--primary-5));
}

.ghd-ref img {
  width: 44px;
  height: 44px;
  object-fit: cover;
  display: block;
}

.gh-meta-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.gh-meta-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
