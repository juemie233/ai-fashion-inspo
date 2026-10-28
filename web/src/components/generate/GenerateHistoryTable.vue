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
} from '@/api/generate'
import { fetchRun } from '@/api/generate'
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
}>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'retry', id: number): void
  (e: 'remove', id: number): void
  (e: 'page-change', page: number): void
  (e: 'update:filters', filters: GenerationHistoryQuery): void
  (e: 'import-selected', runId: number, filenames: string[]): void
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
    props.filters.until,
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
defineExpose({ resetPicked, onDateRangeChange })
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成历史</span>
    <span class="gen-history-hint">点行首箭头看完整描述、参数与全部候选图</span>
    <a-button size="mini" :loading="loading" @click="emit('refresh')">刷新</a-button>
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
    <span class="gen-history-hint" style="margin-left: auto">共 {{ total }} 条</span>
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
      <!-- 单行省略；悬停看全文，或用行首箭头展开完整详情 -->
      <a-table-column title="描述" data-index="prompt" :width="340" ellipsis tooltip />
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
          <a-tag v-if="record.imported_count" size="small" color="green" class="gh-imported">
            已入库 {{ record.imported_count }}
          </a-tag>
        </template>
      </a-table-column>
      <a-table-column title="时间" :width="150">
        <template #cell="{ record }">
          <span style="white-space: nowrap">{{ formatDateTime(record.created_at) }}</span>
        </template>
      </a-table-column>
      <a-table-column title="操作" :width="210">
        <template #cell="{ record }">
          <a-button size="mini" :loading="submitting" @click="emit('retry', record.id)">
            同参重跑
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
          </div>
        </div>
        <div v-if="record.error" class="ghd-row">
          <span class="ghd-label">失败原因</span>
          <div class="ghd-text ghd-error">{{ record.error }}</div>
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
</style>
