/** 本地出图的状态机：就绪探测 → 提交 → 轮询 → 候选区 → 确认入库。
 *
 * 设计约束（与项目既有约定一致）：
 * - 轮询只在「非终态」时进行，进入终态立刻停（`usePolling`）；
 * - 离开页面/组件卸载自动停止轮询；
 * - 提交后保留 `seed`，失败可原样重试（可复现）。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  cancelRun,
  confirmImport,
  createRun,
  discardRun,
  fetchRun,
  fetchStatus,
  listRuns,
  previewPrompt,
  regenerateCandidate,
  retryRun,
  type CreateRunPayload,
  type GenerateStatus,
  type GenerationCandidate,
  type GenerationRunBrief,
  type GenerationRun,
  type GenerationTaskInfo,
  type PromptPreview,
  type GenerationRunDetail,
} from '@/api/generate'
import { usePolling } from '@/composables/usePolling'

/** 非终态集合：只有这些状态才需要继续轮询。 */
const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_vram'])

export function useImageGen() {
  const status = ref<GenerateStatus | null>(null)
  const statusLoading = ref(false)
  const submitting = ref(false)
  const runId = ref<number | null>(null)
  const detail = ref<GenerationRunDetail | null>(null)
  const lastSeed = ref<number | null>(null)
  const selected = ref<Set<string>>(new Set())

  /** 清空当前批次。删除批次后必须调用：`run`/`candidates` 都是 `detail` 的 computed，
   * 不清就会让详情区停在一个已不存在的批次上，用户还能点「确认入库」「同参重跑」。 */
  function reset() {
    detail.value = null
    selected.value = new Set()
    lastSeed.value = null
  }
  /** 生成历史（最近 20 批），用于「同参重跑」入口 */
  const history = ref<GenerationRunBrief[]>([])
  const historyLoading = ref(false)

  const run = computed<GenerationRun | null>(() => detail.value?.run ?? null)
  const candidates = computed<GenerationCandidate[]>(() => detail.value?.items ?? [])
  const active = computed(() => !!run.value && ACTIVE_STATUSES.has(run.value.status))
  /** 任务侧信息（状态/进度/result），null 表示接口没返回（旧数据或任务已被清理）。 */
  const taskInfo = computed<GenerationTaskInfo | null>(() => detail.value?.task ?? null)

  /** 任务阶段（runner 写在 task.result 里：stage / waited_seconds / vram_free_gib）。 */
  const taskStage = computed(() => {
    const result = taskInfo.value?.result
    return {
      stage: typeof result?.stage === 'string' ? result.stage : '',
      waited: Number(result?.waited_seconds ?? 0),
      vramFree: typeof result?.vram_free_gib === 'number' ? result.vram_free_gib : null,
    }
  })

  /** 生成中的人话说明（含「等待显存」的秒数与占用者）。 */
  const stageText = computed(() => {
    const r = run.value
    if (!r) return ''
    if (r.status === 'waiting_vram') {
      const { waited, vramFree } = taskStage.value
      const free = vramFree == null ? '' : `，当前空闲 ${vramFree} GiB`
      return `等待显存（已等 ${waited} 秒${free}）：Ollama 正占着 GPU，可执行 ollama stop 或稍候`
    }
    if (r.status === 'queued') return '排队中（等待 worker 认领）'
    if (r.status === 'running') return `生成中 ${r.done}/${r.total}`
    if (r.status === 'done') return `已完成 ${r.done} 张，勾选后确认入库`
    if (r.status === 'cancelled') return '已取消'
    if (r.status === 'failed') return r.error || '生成失败'
    return r.status
  })

  const { start: startPoll, stop: stopPoll } = usePolling({
    intervalMs: 1500,
    immediate: false,
    callback: async () => {
      if (runId.value === null) return
      try {
        detail.value = await fetchRun(runId.value)
        if (!active.value) stopPoll()
      } catch {
        stopPoll()
      }
    },
  })

  async function loadStatus(): Promise<void> {
    statusLoading.value = true
    try {
      status.value = await fetchStatus()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '读取出图环境失败'))
      status.value = null
    } finally {
      statusLoading.value = false
    }
  }

  async function submit(payload: CreateRunPayload): Promise<number | null> {
    submitting.value = true
    selected.value = new Set()
    try {
      const out = await createRun(payload)
      runId.value = out.run_id
      lastSeed.value = out.seed
      Message.success(`已提交：正在生成 ${out.total} 张（seed ${out.seed}）`)
      detail.value = await fetchRun(out.run_id)
      startPoll()
      return out.run_id
    } catch (e) {
      Message.error(getApiErrorMessage(e, '提交出图任务失败'))
      return null
    } finally {
      submitting.value = false
    }
  }

  function toggle(filename: string): void {
    const next = new Set(selected.value)
    if (next.has(filename)) next.delete(filename)
    else next.add(filename)
    selected.value = next
  }

  function selectAll(): void {
    selected.value = new Set(candidates.value.filter((c) => !c.imported).map((c) => c.filename))
  }

  function clearSelection(): void {
    selected.value = new Set()
  }

  async function confirm(): Promise<number> {
    if (runId.value === null || selected.value.size === 0) return 0
    try {
      const out = await confirmImport(runId.value, [...selected.value])
      if (out.imported) {
        Message.success(
          `已入库 ${out.imported} 张${out.skipped ? `（跳过 ${out.skipped} 张已存在）` : ''}`,
        )
      } else {
        Message.info('选中的图都已在库中')
      }
      detail.value = await fetchRun(runId.value)
      selected.value = new Set()
      return out.imported
    } catch (e) {
      Message.error(getApiErrorMessage(e, '确认入库失败'))
      return 0
    }
  }

  async function cancel(): Promise<void> {
    if (runId.value === null) return
    try {
      await cancelRun(runId.value)
      Message.info('已请求取消（已生成的图会保留在候选区）')
      detail.value = await fetchRun(runId.value)
      stopPoll()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '取消失败'))
    }
  }

  /**
   * 从素材反推 prompt（读标签/正文/主色），返回拼好的描述串。
   *
   * 放在 composable 里而不是 view 里：视图只做编排，且 ts-prune 看不到 .vue 引用，
   * 直接在视图里调 api 会被误判成死代码（项目死代码门禁对 .vue 是盲的）。
   */
  async function loadHistory(): Promise<void> {
    historyLoading.value = true
    try {
      const out = await listRuns(20)
      history.value = out.items
    } catch (e) {
      Message.error(getApiErrorMessage(e, '读取生成历史失败'))
    } finally {
      historyLoading.value = false
    }
  }

  /**
   * 同参重跑：新建批次（历史保留），seed=0 随机、>0 复现。
   *
   * 为什么不在原批次上重跑：原批次的 done/total 与候选文件会被覆盖，
   * 「这批参数产出了哪张图」就查不清了。
   */
  async function retry(sourceRunId: number, seed = 0): Promise<number | null> {
    submitting.value = true
    try {
      const out = await retryRun(sourceRunId, seed)
      runId.value = out.run_id
      lastSeed.value = out.seed
      Message.success(`已按原参数重跑（seed ${out.seed}）`)
      detail.value = await fetchRun(out.run_id)
      startPoll()
      await loadHistory()
      return out.run_id
    } catch (e) {
      Message.error(getApiErrorMessage(e, '重跑失败'))
      return null
    } finally {
      submitting.value = false
    }
  }

  /**
   * 对**某一张**候选图换 seed 重出。
   *
   * 为什么不做「整批重跑」：用户通常只对其中一张不满意（构图/手崩），整批重跑会连同
   * 已经满意、甚至已入库的那几张一起重出，白烧算力还可能重出更差的。
   */
  async function regenerate(item: GenerationCandidate): Promise<number | null> {
    if (runId.value === null) return null
    submitting.value = true
    try {
      const out = await regenerateCandidate(runId.value, item.filename)
      Message.success(`已用新 seed ${out['seed']} 重出（原 seed ${out['original_seed']}）`)
      // 切到新批次（只含 1 张），原批次仍留在历史里
      runId.value = out.run_id
      lastSeed.value = out.seed
      detail.value = await fetchRun(out.run_id)
      startPoll()
      await loadHistory()
      return out.run_id
    } catch (e) {
      Message.error(getApiErrorMessage(e, '重出失败'))
      return null
    } finally {
      submitting.value = false
    }
  }

  async function promptFrom(ids: string[]): Promise<string | null> {
    if (!ids.length) return null
    try {
      const out: PromptPreview = await previewPrompt(ids)
      return out.prompt
    } catch (e) {
      Message.error(getApiErrorMessage(e, '从素材反推描述失败'))
      return null
    }
  }

  async function discard(): Promise<void> {
    if (runId.value === null) return
    try {
      await discardRun(runId.value)
      Message.info('已丢弃本批候选图')
      detail.value = null
      runId.value = null
      stopPoll()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '丢弃失败'))
    }
  }

  return {
    reset,
    // 状态
    status,
    statusLoading,
    submitting,
    run,
    runId,
    candidates,
    selected,
    active,
    stageText,
    taskStage,
    taskInfo,
    lastSeed,
    history,
    historyLoading,
    // 动作
    loadStatus,
    submit,
    toggle,
    selectAll,
    clearSelection,
    confirm,
    cancel,
    promptFrom,
    loadHistory,
    retry,
    regenerate,
    discard,
    stopPoll,
  }
}
