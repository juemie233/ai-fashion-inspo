<script setup lang="ts">
/** 本地出图页：填参数 → 生成 → 候选区勾选 → 确认入库。
 *
 * 为什么两段式（生成 / 入库分开）：本项目一贯「宁缺毋滥、手动确认」——
 * 生成 4 张里通常只要 1~2 张，直接入库会让废图占库容、进统计、还进打标队列烧算力。
 *
 * 显存现实（实测）：16 GB 卡上 1024² 峰值已到 15.1 GiB，所以尺寸只给预设、张数上限 4，
 * 且生成时会与打标任务互斥（后端 GPU 互斥组），必要时排队等 Ollama 释放显存。
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { readCachedModelsMeta, useImageGen } from '@/composables/useImageGen'
import GenerateCandidatesPanel from '@/components/generate/GenerateCandidatesPanel.vue'
import GenerateHistoryTable from '@/components/generate/GenerateHistoryTable.vue'
import GenerateParamForm from '@/components/generate/GenerateParamForm.vue'
import GenerateCompareModal from '@/components/generate/GenerateCompareModal.vue'
import ReferenceGalleryModal from '@/components/generate/ReferenceGalleryModal.vue'
import { fetchInspirations } from '@/api/inspirations'
import { useReferenceItems } from '@/composables/useReferenceItems'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import { confirmImport, discardRun, type GenerationHistoryQuery } from '@/api/generate'
import { getApiErrorMessage } from '@/utils/apiError'

const route = useRoute()
/** 删除某条生成记录（连同批次目录里的候选图）；已入库的素材不受影响 */
const deletingId = ref<number | null>(null)

async function removeRun(id: number) {
  deletingId.value = id
  try {
    const r = await discardRun(id)
    Message.success(`已删除（清理 ${r.removed_files ?? 0} 个文件）`)
    // 删掉的正是当前打开的批次：清空详情区，否则用户还能对已删除的批次点「确认入库」
    if (run.value?.id === id) reset()
    if (historyVisible.value) await refreshHistory()
    else historyDirty.value = true
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
  } finally {
    deletingId.value = null
  }
}

const {
  status,
  statusLoading,
  submitting,
  run,
  candidates,
  selected,
  active,
  stageText,
  lastSeed,
  reset,
  history,
  historyLoading,
  historyTotal,
  historyPage,
  historyPageSize,
  historyFilters,
  setHistoryPage,
  setHistoryFilters,
  loadStatus,
  submit,
  toggle,
  selectAll,
  clearSelection,
  confirm,
  cancel,
  promptFrom,
  discard,
  stopPoll,
  loadHistory,
  retry,
  regenerate,
} = useImageGen()

// ── 候选图大图浏览 ──
/** 灯箱开关与起始索引：缩略图看不清面料/版型，点开可按 100% 看细节并左右翻整批 */
const lightboxVisible = ref(false)
const lightboxIndex = ref(0)
const candidateUrls = computed(() => candidates.value.map((c) => c.url))

function openLightbox(index: number): void {
  if (!candidates.value.length) return
  lightboxIndex.value = Math.max(0, Math.min(index, candidates.value.length - 1))
  lightboxVisible.value = true
}

/** 候选图 URL 由后端直接给出（/api/generate/...），不能再套素材库的 /api/files/ 前缀 */
function resolveCandidateUrl(path: string): string {
  return path
}

const ready = computed(() => status.value?.ready === true)

/** 档位目录缓存（上次探测成功的 MODEL_SPECS 快照）：探测要 4.4 秒，先用它把档位列出来 */
const cachedModelsMeta = readCachedModelsMeta()

// ── 生成历史：默认收起 ──
/** 是否展开历史（**默认隐藏**：出图页的主线是「填参数 → 生成 → 勾选入库」，历史只偶尔用） */
const historyVisible = ref(false)
/** 是否已加载过（懒加载：收起时不发请求，也不因轮询白拉） */
const historyLoaded = ref(false)
/** 收起期间发生过变更（批次完成 / 删除记录）→ 下次展开时补拉 */
const historyDirty = ref(false)

/** 拉历史（只在这两种时机调用：首次展开、变更后再展开） */
async function refreshHistory(): Promise<void> {
  await loadHistory()
  historyLoaded.value = true
  historyDirty.value = false
}

function toggleHistory(): void {
  historyVisible.value = !historyVisible.value
  if (historyVisible.value && (!historyLoaded.value || historyDirty.value)) void refreshHistory()
}

/** 收起历史时清掉筛选与页码：下次展开是「全部历史第 1 页」的可预期状态 */
function closeHistory(): void {
  historyVisible.value = false
  if (
    historyFilters.value.model_key ||
    historyFilters.value.status ||
    historyFilters.value.has_imported ||
    historyFilters.value.q
  ) {
    void setHistoryFilters({})
  }
}

/** 当前批次本身变了（入库/丢弃）：历史行会跟着变，按可见性决定「现在刷」还是「下次刷」 */
function touchHistory(): void {
  if (historyVisible.value) void refreshHistory()
  else historyDirty.value = true
}

/** 历史表实例：从历史入库成功后要清掉那一行的勾选并让它重拉候选 */
const historyTable = ref<InstanceType<typeof GenerateHistoryTable> | null>(null)
/** 历史区入库中（展开行的按钮 loading） */
const historyImporting = ref(false)

/** 从历史页直接入库选中的候选（与候选区入库共用同一个接口） */
async function onImportFromHistory(runId: number, filenames: string[]): Promise<void> {
  historyImporting.value = true
  try {
    const result = await confirmImport(runId, filenames)
    const imported = result.imported ?? 0
    if (imported > 0) Message.success(`已入库 ${imported} 张`)
    else Message.info('这张已经入库过了（按内容哈希去重）')
    historyTable.value?.resetPicked(runId)
    await refreshHistory()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '入库失败'))
  } finally {
    historyImporting.value = false
  }
}

/** 历史区筛选变化（工具栏回抛）：回到第 1 页重新拉 */
async function onHistoryFilters(next: GenerationHistoryQuery): Promise<void> {
  await setHistoryFilters(next)
}

/** 确认入库：入库张数会体现在历史行里 */
async function onConfirm(): Promise<void> {
  const imported = await confirm()
  if (imported > 0) touchHistory()
}

/** 丢弃本批：这行记录随后就没了，历史必须跟上（否则展开时还看得到已丢弃的批次） */
async function onDiscard(): Promise<void> {
  await discard()
  touchHistory()
}

/** 批次进入终态：展开时立刻刷新；收起时只打脏标记，避免看不见的表格白拉接口 */
watch(
  () => run.value?.status,
  (st) => {
    if (!st || !['done', 'failed', 'cancelled'].includes(st)) return
    if (historyVisible.value) void refreshHistory()
    else historyDirty.value = true
  },
)

/** 从素材详情「用这张图改款」跳进来时带的参考素材 ID（交给参数面板预填并切档位） */
const initialRefIds = typeof route.query.ref === 'string' ? route.query.ref.trim() : ''
/** 从素材详情「图生图（未审查）」跳进来时显式指定的档位（缺省按图生图自动挑就绪档位） */
const initialModelKey = typeof route.query.model === 'string' ? route.query.model.trim() : ''

// ── 已选参考图：拉缩略图供「描述框上方」的缩略图条与浏览大图 ──
const referenceGalleryVisible = ref(false)
const referenceGalleryIndex = ref(0)

/** 拉一批参考素材的元数据，并按传入顺序重排（接口返回顺序不保证，实测是倒序） */
async function fetchReferenceItems(
  ids: string[],
): Promise<Array<{ id: string; file_path: string; thumbnail_path?: string | null }>> {
  const data = await fetchInspirations({ ids: ids.join(','), size: ids.length })
  const byId = new Map(data.items.map((i) => [i.id, i]))
  return ids
    .map((id) => byId.get(id))
    .filter((i): i is (typeof data.items)[number] => Boolean(i))
    .map((i) => ({
      id: i.id,
      file_path: i.file_path,
      thumbnail_path: i.thumbnail_path,
    }))
}

/**
 * 缩略图数据：**防抖 300ms + 丢过期响应**（见 `useReferenceItems`）。
 *
 * 手输 id 时 v-model 每敲一个字符都抛一次选择变化，不做防抖就会一个字符发一次请求，
 * 且响应乱序会让缩略图条闪来闪去。
 */
const { items: referenceItems, set: setReferenceIds } = useReferenceItems(fetchReferenceItems)

function onReferenceIdsChange(ids: string[]): void {
  setReferenceIds(ids)
}

function openReferenceGallery(index: number): void {
  if (!referenceItems.value.length) return
  referenceGalleryIndex.value = index
  referenceGalleryVisible.value = true
}

// ── 对比视图（与「点图看大图」的灯箱是两条独立路径：各自的索引与状态）──
const compareVisible = ref(false)
const compareIndex = ref(0)

/** 对比用的候选图（只挑弹窗需要的字段，避免把整条 run 传进去） */
const compareItems = computed(() =>
  candidates.value.map((item) => ({
    url: item.url,
    seed: item.seed,
    filename: item.filename,
    imported: item.imported,
  })),
)

/** 本批用到的参考素材 id：后端把 params.reference_ids 存下来了（顺序即参考顺序） */
const compareRefIds = computed<string[]>(() => {
  const params = run.value?.params as { reference_ids?: unknown } | undefined
  const ids = params?.reference_ids
  return Array.isArray(ids) ? ids.map(String).filter(Boolean) : []
})

function openCompare(index: number): void {
  compareIndex.value = index
  compareVisible.value = true
}

onMounted(() => {
  void loadStatus()
  // 历史默认收起 → 不在挂载时拉（首次展开时 refreshHistory 才发请求）
})
onUnmounted(stopPoll)
</script>

<template>
  <div class="gen-page">
    <div class="gen-head">
      <div>
        <h2 class="gen-title">本地出图</h2>
        <div class="gen-sub">
          用本机 ComfyUI 生成穿搭图；生成结果先进候选区，勾选后才入库（默认待审核、标记「AI
          生成」）。
        </div>
      </div>
      <a-button :loading="statusLoading" @click="loadStatus">刷新环境</a-button>
    </div>

    <!-- 环境未就绪：给可执行引导，而不是让用户点了才失败 -->
    <a-alert v-if="status && !ready" type="warning" class="gen-alert">
      {{ status.reason || '出图环境未就绪' }}
      <div class="gen-hint">
        启动方式：双击 <code>D:\AI\ComfyUI\run_nvidia_gpu.bat</code>，等浏览器能打开
        <code>http://127.0.0.1:8188</code> 后点「刷新环境」。
      </div>
    </a-alert>
    <a-alert v-else-if="status" type="success" class="gen-alert">
      ComfyUI {{ status.comfyui.version }} 就绪 · 显存 {{ status.comfyui.vram_free_gib }} /
      {{ status.comfyui.vram_total_gib }} GiB 可用
      <span v-if="status.ollama_resident.length" class="gen-hint">
        · Ollama 占用：{{
          status.ollama_resident.map((m) => m.name).join('、')
        }}（出图会等待其释放）
      </span>
    </a-alert>

    <div class="gen-grid">
      <!-- 左：参数 -->
      <a-card class="gen-card" :bordered="false">
        <GenerateParamForm
          :status="status"
          :ready="ready"
          :submitting="submitting"
          :active="active"
          :initial-ref-ids="initialRefIds"
          :initial-model-key="initialModelKey"
          :models-meta-fallback="cachedModelsMeta"
          :prompt-from="promptFrom"
          :reference-items="referenceItems"
          @submit="submit"
          @cancel="cancel"
          @reference-ids-change="onReferenceIdsChange"
          @browse-references="openReferenceGallery"
        />
      </a-card>

      <!-- 右：进度与候选 -->
      <a-card class="gen-card" :bordered="false">
        <GenerateCandidatesPanel
          :run="run"
          :candidates="candidates"
          :selected="selected"
          :stage-text="stageText"
          :last-seed="lastSeed"
          :submitting="submitting"
          @preview="openLightbox"
          @toggle="toggle"
          @regenerate="regenerate"
          @select-all="selectAll"
          @clear-selection="clearSelection"
          @confirm="onConfirm"
          @discard="onDiscard"
          @compare="openCompare"
        />
      </a-card>
    </div>

    <!-- 候选图大图浏览：复用素材库的灯箱（缩放/拖动/左右切换都有） -->
    <ImageLightbox
      :show="lightboxVisible"
      :image-paths="candidateUrls"
      :initial-index="lightboxIndex"
      :url-resolver="resolveCandidateUrl"
      @close="lightboxVisible = false"
    />

    <!-- 已选参考图的大图浏览（从描述框上方的缩略图条点进来） -->
    <ReferenceGalleryModal
      v-model:visible="referenceGalleryVisible"
      :items="referenceItems"
      :start-index="referenceGalleryIndex"
    />

    <!-- 对比：参考图 ↔ 生成图（独立弹窗；打开它不会影响候选勾选与灯箱状态） -->
    <GenerateCompareModal
      :visible="compareVisible"
      :candidates="compareItems"
      :initial-index="compareIndex"
      :reference-ids="compareRefIds"
      @update:visible="compareVisible = $event"
    />

    <!-- 生成历史：默认收起（同参重跑入口；历史只存参数，图在候选区，过期会被清理）。
         收起时连表格都不渲染、也不发请求——出图页的主线是「填参数 → 生成 → 入库」 -->
    <div class="gen-history-toggle">
      <a-button size="mini" @click="historyVisible ? closeHistory() : toggleHistory()">
        {{ historyVisible ? '收起生成历史' : '展开生成历史' }}
        <span v-if="!historyVisible && history.length">（{{ history.length }} 条）</span>
      </a-button>
      <span class="gen-hint">
        同参重跑 / 删除记录；默认隐藏，展开才加载（不影响当前批次的候选与勾选）
      </span>
    </div>
    <a-card v-if="historyVisible" class="gen-card gen-history" :bordered="false">
      <GenerateHistoryTable
        ref="historyTable"
        :history="history"
        :total="historyTotal"
        :page="historyPage"
        :size="historyPageSize"
        :filters="historyFilters"
        :loading="historyLoading"
        :submitting="submitting"
        :deleting-id="deletingId"
        :status="status"
        :importing="historyImporting"
        @refresh="refreshHistory"
        @retry="retry"
        @remove="removeRun"
        @page-change="setHistoryPage"
        @update:filters="onHistoryFilters"
        @import-selected="onImportFromHistory"
      />
    </a-card>
  </div>
</template>

<style scoped>
.gen-page {
  padding: 16px;
}
.gen-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.gen-title {
  margin: 0 0 4px;
  font-size: 18px;
}
.gen-sub {
  color: #8a8f99;
  font-size: 12px;
}
.gen-alert {
  margin-bottom: 12px;
}
.gen-hint {
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}
.gen-hint code {
  padding: 0 3px;
  border-radius: 3px;
  background: #f2f3f5;
}
.gen-grid {
  display: grid;
  grid-template-columns: minmax(320px, 420px) 1fr;
  gap: 12px;
}
@media (max-width: 900px) {
  .gen-grid {
    grid-template-columns: 1fr;
  }
}
.gen-card {
  background: #fff;
}
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
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
.gen-history {
  margin-top: 8px;
}
.gen-history-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
</style>
