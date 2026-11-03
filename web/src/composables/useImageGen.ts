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
  fetchRunStats,
  updateRunMeta as updateRunMetaApi,
  type CreateRunPayload,
  type GenerateStatus,
  type GenerationCandidate,
  type GenerationHistoryQuery,
  type GenerationRunMetaPatch,
  type GenerationRunStats,
  type GenerationRunBrief,
  type GenerationRun,
  type GenerationTaskInfo,
  type PromptPreview,
  type GenerationRunDetail,
} from '@/api/generate'
import { usePolling } from '@/composables/usePolling'

/** 非终态集合：只有这些状态才需要继续轮询。 */
const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_vram'])

/** 多姿态组的聚合状态（与后端列表口径一致：有在跑就显示在跑）。 */
function _mergedStatus(runs: GenerationRun[]): string {
  const statuses = runs.map((run) => run.status)
  for (const active of ['running', 'waiting_vram', 'queued']) {
    if (statuses.includes(active)) return active
  }
  if (statuses.length && statuses.every((status) => status === 'failed')) return 'failed'
  if (statuses.length && statuses.every((status) => status === 'cancelled')) return 'cancelled'
  return 'done'
}

/** 生成多姿态分组 id（32 位小写十六进制，后端 schema 校验格式）。 */
function _newGroupId(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return uuid.replace(/-/g, '').toLowerCase()
  const fallback = `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`
  return fallback.padEnd(32, '0').slice(0, 32)
}

/** 档位目录缓存：`/generate/status` 要探测 ComfyUI + 权重文件 + Ollama，实测 **4.4 秒**。
 *
 * 这期间如果拿「兜底两档」先渲染，用户会看到「先出现两个档位、几秒后第三个冒出来」，
 * 而且从素材详情带 `?model=qwen_2_1_uc_i2i` 跳进来时，未审查档位此刻还不在列表里，
 * 请求会被丢弃 → 到页面时没选中未审查版（真实缺陷）。
 *
 * 所以把**档位目录**（label / kind / uncensored / notice，来自后端 MODEL_SPECS，是静态目录）
 * 缓存下来：进页面先用它渲染档位，就绪状态仍等探测结果（`ready` 一律先按未就绪处理）。
 */
const MODELS_META_CACHE_KEY = 'fashion-inspo:generate:models-meta'

/** 读缓存的档位目录（无缓存/损坏返回 null） */
export function readCachedModelsMeta(): GenerateStatus['models_meta'] | null {
  try {
    const raw = localStorage.getItem(MODELS_META_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return parsed as GenerateStatus['models_meta']
  } catch {
    return null
  }
}

/** 写档位目录缓存（探测成功后调用；写失败不影响主流程） */
export function writeCachedModelsMeta(meta: GenerateStatus['models_meta'] | undefined): void {
  if (!meta || !Object.keys(meta).length) return
  try {
    localStorage.setItem(MODELS_META_CACHE_KEY, JSON.stringify(meta))
  } catch {
    /* 隐私模式或配额满：忽略，下次照样能探测 */
  }
}

export function useImageGen() {
  const status = ref<GenerateStatus | null>(null)
  const statusLoading = ref(false)
  const submitting = ref(false)
  const runId = ref<number | null>(null)
  const detail = ref<GenerationRunDetail | null>(null)
  /** 多姿态提交：同一次提交的多个独立批次 id（按姿态提交顺序；空 = 单批次） */
  const groupRunIds = ref<number[]>([])
  /** 多姿态提交：各批次详情（与 groupRunIds 同序；用于合并候选图与进度） */
  const groupDetails = ref<GenerationRunDetail[]>([])
  const lastSeed = ref<number | null>(null)
  const selected = ref<Set<string>>(new Set())

  /** 清空当前批次。删除批次后必须调用：`run`/`candidates` 都是 `detail` 的 computed，
   * 不清就会让详情区停在一个已不存在的批次上，用户还能点「确认入库」「同参重跑」。 */
  function reset() {
    detail.value = null
    groupDetails.value = []
    groupRunIds.value = []
    selected.value = new Set()
    lastSeed.value = null
    runId.value = null
  }

  /** 清空多姿态组状态（切回单批次/重新提交时用） */
  function clearGroup() {
    groupDetails.value = []
    groupRunIds.value = []
  }
  /** 生成历史（服务端分页 + 筛选）：列表、总数、页码与筛选条件 */
  const history = ref<GenerationRunBrief[]>([])
  const historyLoading = ref(false)
  const historyTotal = ref(0)
  const historyPage = ref(1)
  /** 每页条数（历史区一屏约 10 行 + 分页，20 条足够且请求轻） */
  const HISTORY_PAGE_SIZE = 20
  /** 筛选条件：档位 / 状态 / 只看已入库 / 关键字（后端执行，前端不二次过滤） */
  const historyFilters = ref<GenerationHistoryQuery>({})
  /** 近 30 天统计（历史区顶部数字卡；收起时不拉） */
  const historyStats = ref<GenerationRunStats | null>(null)
  /** 候选保留天数（后端给，前端算「图还剩 N 天」） */
  const historyKeepDays = ref(0)

  const run = computed<GenerationRun | null>(() => {
    if (groupRunIds.value.length) {
      const runs = groupDetails.value.map((item) => item.run)
      if (!runs.length) return null
      const last = runs[runs.length - 1]
      return {
        ...last,
        // 合成一个「组批次」给现有组件用（进度/状态/文案），底层批次仍各自独立
        status: _mergedStatus(runs),
        total: runs.reduce((sum, item) => sum + item.total, 0),
        done: runs.reduce((sum, item) => sum + item.done, 0),
        failed: runs.reduce((sum, item) => sum + item.failed, 0),
        error: runs.find((item) => item.error)?.error ?? null,
        task_id: null,
      }
    }
    return detail.value?.run ?? null
  })

  /** 多姿态合并后的候选图：每张都带 run_id（入库时要按批调用接口）。 */
  const candidates = computed<GenerationCandidate[]>(() => {
    if (groupRunIds.value.length) {
      const merged: GenerationCandidate[] = []
      for (const item of groupDetails.value) {
        const batchRunId = item.run.id
        for (const candidate of item.items ?? []) {
          merged.push({
            ...candidate,
            run_id: batchRunId,
            // 同一次提交里每个姿态的文件名可能相同（01_seed-0.png），选择键必须带批次
            select_key: `${batchRunId}:${candidate.filename}`,
          })
        }
      }
      return merged
    }
    const singleRunId = detail.value?.run?.id
    return (detail.value?.items ?? []).map((candidate) =>
      singleRunId === undefined ? candidate : { ...candidate, run_id: singleRunId },
    )
  })
  const active = computed(() => !!run.value && ACTIVE_STATUSES.has(run.value.status))
  /** 任务侧信息（状态/进度/result），null 表示接口没返回（旧数据或任务已被清理）。 */
  const taskInfo = computed<GenerationTaskInfo | null>(() => detail.value?.task ?? null)
  /** 本次是否为多姿态合并展示（>1 个独立批次） */
  const isGroup = computed(() => groupRunIds.value.length > 1)

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
    if (groupRunIds.value.length > 1) {
      const runs = groupDetails.value.map((item) => item.run)
      if (!runs.length) return '正在读取多姿态结果…'
      const pending = runs.filter((item) => ACTIVE_STATUSES.has(item.status)).length
      const failedRuns = runs.filter((item) => item.status === 'failed').length
      if (pending > 0) {
        return `多姿态生成中：已完成 ${r.done}/${r.total} 张（${pending} 个姿态仍在生成）`
      }
      if (failedRuns === runs.length) {
        return `全部 ${failedRuns} 个姿态生成失败：${runs.find((item) => item.error)?.error || '未知错误'}`
      }
      if (r.done >= r.total) {
        return `已完成 ${r.done} 张（${runs.length} 个姿态合并显示），勾选后确认入库`
      }
      return `多姿态生成结束：成功 ${r.done} 张，失败 ${failedRuns} 个姿态`
    }
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

  /** 拉取当前展示所需的详情（多姿态组逐批拉，单批次只拉一个）。 */
  async function refreshCurrentDetails(): Promise<void> {
    const ids = groupRunIds.value.length
      ? groupRunIds.value
      : runId.value === null
        ? []
        : [runId.value]
    if (!ids.length) return
    const details = await Promise.all(ids.map((id) => fetchRun(id)))
    groupDetails.value = details
    detail.value = details[details.length - 1] ?? null
  }

  const { start: startPoll, stop: stopPoll } = usePolling({
    intervalMs: 1500,
    immediate: false,
    callback: async () => {
      if (groupRunIds.value.length === 0 && runId.value === null) return
      try {
        await refreshCurrentDetails()
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
      writeCachedModelsMeta(status.value?.models_meta)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '读取出图环境失败'))
      status.value = null
    } finally {
      statusLoading.value = false
    }
  }

  /** 云端多姿态：每个姿态一个独立 run，最多 2 并发（控制方舟 429）。
   *
   * 与单次 submit 的区别：不覆盖正在展示的批次，把本次提交的所有成功批次合并展示
   * （候选区一次显示多个姿态的结果）；底层每批仍独立排队/执行/计费。
   * 失败项列在提示里，用户可单独重试；已成功的不会重复提交。
   */
  async function submitBatch(
    payloads: CreateRunPayload[],
  ): Promise<{ succeeded: number; failed: number }> {
    submitting.value = true
    selected.value = new Set()
    clearGroup()
    const succeeded: Array<{ run_id: number; seed: number | null }> = []
    const failures: string[] = []
    // 同一次「多姿态提交」的批次共用一个 group_id：历史列表与候选区据此合并展示
    const groupId = payloads.length > 1 ? _newGroupId() : null
    const groupedPayloads = payloads.map((payload) =>
      groupId ? { ...payload, group_id: groupId } : payload,
    )
    try {
      for (let i = 0; i < groupedPayloads.length; i += 2) {
        const chunk = groupedPayloads.slice(i, i + 2)
        const results = await Promise.allSettled(chunk.map((payload) => createRun(payload)))
        results.forEach((result) => {
          if (result.status === 'fulfilled') succeeded.push(result.value)
          else failures.push(getApiErrorMessage(result.reason, '提交失败'))
        })
      }
      if (succeeded.length > 0) {
        groupRunIds.value = succeeded.map((item) => item.run_id)
        runId.value = succeeded[succeeded.length - 1].run_id
        lastSeed.value = null
        await refreshCurrentDetails()
        startPoll()
        Message.success(`已提交 ${succeeded.length} 个云端批次，正在逐个生成`)
      }
      if (failures.length > 0) {
        Message.error(
          `${failures.length} 个批次提交失败：${failures[0]}${failures.length > 1 ? '（等）' : ''}`,
        )
      }
      return { succeeded: succeeded.length, failed: failures.length }
    } finally {
      submitting.value = false
    }
  }

  async function submit(payload: CreateRunPayload): Promise<number | null> {
    submitting.value = true
    selected.value = new Set()
    clearGroup()
    try {
      const out = await createRun(payload)
      runId.value = out.run_id
      lastSeed.value = out.seed
      // 云端档位无 seed（每次随机），不要把 null 打印成「seed null」
      const seedSuffix = out.seed === null ? '（云端每次随机）' : `（seed ${out.seed}）`
      Message.success(`已提交：正在生成 ${out.total} 张${seedSuffix}`)
      await refreshCurrentDetails()
      startPoll()
      return out.run_id
    } catch (e) {
      Message.error(getApiErrorMessage(e, '提交出图任务失败'))
      return null
    } finally {
      submitting.value = false
    }
  }

  function toggle(key: string): void {
    const next = new Set(selected.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    selected.value = next
  }

  /** 候选选择键：多姿态合并时带批次前缀，单批次沿用文件名（兼容既有交互） */
  function _candidateKey(candidate: GenerationCandidate): string {
    return candidate.select_key ?? candidate.filename
  }

  function selectAll(): void {
    selected.value = new Set(
      candidates.value.filter((c) => !c.imported).map((c) => _candidateKey(c)),
    )
  }

  function clearSelection(): void {
    selected.value = new Set()
  }

  async function confirm(): Promise<number> {
    if (selected.value.size === 0) return 0
    // 选中的可能来自多个独立批次（多姿态合并）：按 run_id 分组逐批调用确认入库接口
    const byRun = new Map<number, string[]>()
    for (const candidate of candidates.value) {
      if (!selected.value.has(_candidateKey(candidate))) continue
      const batchRunId = candidate.run_id ?? runId.value
      if (batchRunId === null) continue
      const filenames = byRun.get(batchRunId) ?? []
      filenames.push(candidate.filename)
      byRun.set(batchRunId, filenames)
    }
    if (!byRun.size) return 0
    try {
      let imported = 0
      let skipped = 0
      for (const [batchRunId, filenames] of byRun) {
        const out = await confirmImport(batchRunId, filenames)
        imported += out.imported ?? 0
        skipped += out.skipped ?? 0
      }
      if (imported > 0) {
        Message.success(`已入库 ${imported} 张${skipped ? `（跳过 ${skipped} 张已存在）` : ''}`)
      } else {
        Message.info('选中的图都已在库中')
      }
      await refreshCurrentDetails()
      selected.value = new Set()
      return imported
    } catch (e) {
      Message.error(getApiErrorMessage(e, '确认入库失败'))
      return 0
    }
  }

  async function cancel(): Promise<void> {
    const ids = groupRunIds.value.length
      ? groupRunIds.value
      : runId.value === null
        ? []
        : [runId.value]
    if (!ids.length) return
    try {
      for (const id of ids) await cancelRun(id)
      Message.info('已请求取消（已生成的图会保留在候选区）')
      await refreshCurrentDetails()
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
  /**
   * 拉当前页历史。筛选与分页都由后端执行（`total` 与 `items` 同口径，否则分页会飘）。
   *
   * 注意：调用方（视图）在历史区收起时不调它——收起时连请求都不该发。
   */
  async function loadHistory(): Promise<void> {
    historyLoading.value = true
    try {
      const out = await listRuns({
        page: historyPage.value,
        size: HISTORY_PAGE_SIZE,
        ...historyFilters.value,
      })
      history.value = out.items ?? []
      historyTotal.value = out.total ?? 0
      historyKeepDays.value = out.keep_days ?? 0
    } catch (e) {
      Message.error(getApiErrorMessage(e, '读取生成历史失败'))
    } finally {
      historyLoading.value = false
    }
  }

  /** 翻页（回到顶部由表格自己管，这里只换数据） */
  async function setHistoryPage(page: number): Promise<void> {
    historyPage.value = Math.max(1, page)
    await loadHistory()
  }

  /** 改筛选条件：一律回到第 1 页（否则停在旧页会看到空列表） */
  /** 拉统计卡（与列表分开：统计不随筛选变化，只跟时间窗口走） */
  async function loadHistoryStats(days = 30): Promise<void> {
    try {
      historyStats.value = await fetchRunStats(days)
    } catch {
      // 统计只是辅助信息：失败不打断历史区（保留上一次数据）
    }
  }

  /**
   * 更新批次标注（收藏 / 命名 / 备注）。
   *
   * 成功后**原地替换**列表里那一行，避免整页刷新导致分页位置与展开状态丢失。
   */
  async function updateRunMeta(runId: number, patch: GenerationRunMetaPatch): Promise<boolean> {
    try {
      const updated = await updateRunMetaApi(runId, patch)
      const idx = history.value.findIndex((item) => item.id === runId)
      if (idx >= 0) {
        // 多姿态合并行的标注落在代表批次上：接口返回的是单批次摘要，必须保留
        // run_ids/candidates 等合并字段，否则列表会把整组「打散成一张图」。
        history.value[idx] = history.value[idx].group_id
          ? {
              ...history.value[idx],
              ...updated,
              group_id: history.value[idx].group_id,
              run_ids: history.value[idx].run_ids,
              candidates: history.value[idx].candidates,
              candidate_total: history.value[idx].candidate_total,
            }
          : updated
      }
      // 正在按收藏筛选时，收藏状态一变这行就不符合条件了：重拉当前页，
      // 否则列表会留着一条本该消失的记录，total 也对不上。
      if (
        historyFilters.value.favorite !== undefined &&
        updated.favorite !== historyFilters.value.favorite
      ) {
        await loadHistory()
      }
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存标注失败'))
      return false
    }
  }

  async function setHistoryFilters(next: GenerationHistoryQuery): Promise<void> {
    historyFilters.value = next
    historyPage.value = 1
    await loadHistory()
  }

  /**
   * 同参重跑：新建批次（历史保留），seed=0 随机、>0 复现。
   *
   * 为什么不在原批次上重跑：原批次的 done/total 与候选文件会被覆盖，
   * 「这批参数产出了哪张图」就查不清了。
   */
  async function retry(sourceRunId: number, seed = 0): Promise<number | null> {
    submitting.value = true
    clearGroup()
    try {
      const out = await retryRun(sourceRunId, seed)
      runId.value = out.run_id
      lastSeed.value = out.seed
      Message.success(
        out.seed === null
          ? '已按原参数重新生成（云端每次随机）'
          : `已按原参数重跑（seed ${out.seed}）`,
      )
      await refreshCurrentDetails()
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
   * 对**某一张**候选图换 seed 重出（云端档位 = 按同一素材/姿势重新生成一张）。
   *
   * 为什么不做「整批重跑」：用户通常只对其中一张不满意（构图/手崩），整批重跑会连同
   * 已经满意、甚至已入库的那几张一起重出，白烧算力还可能重出更差的。
   * 多姿态合并展示时，候选自带 `run_id`：对哪一批的图操作就提交哪一批。
   */
  async function regenerate(item: GenerationCandidate): Promise<number | null> {
    const sourceRunId = item.run_id ?? runId.value
    if (sourceRunId === null) return null
    submitting.value = true
    clearGroup()
    try {
      const out = await regenerateCandidate(sourceRunId, item.filename)
      // 云端无 seed：文案改「重新生成」，不要打印 new/original seed
      Message.success(
        out.seed === null
          ? '已按同一素材/姿势重新生成一张（云端每次随机）'
          : `已用新 seed ${out['seed']} 重出（原 seed ${out['original_seed']}）`,
      )
      // 切到新批次（只含 1 张），原批次仍留在历史里
      runId.value = out.run_id
      lastSeed.value = out.seed
      await refreshCurrentDetails()
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
    const ids = groupRunIds.value.length
      ? groupRunIds.value
      : runId.value === null
        ? []
        : [runId.value]
    if (!ids.length) return
    try {
      for (const id of ids) await discardRun(id)
      Message.info(isGroup.value ? '已丢弃本次多姿态的全部候选图' : '已丢弃本批候选图')
      reset()
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
    isGroup,
    groupRunIds,
    stageText,
    taskStage,
    taskInfo,
    lastSeed,
    history,
    historyLoading,
    historyTotal,
    historyPage,
    historyPageSize: HISTORY_PAGE_SIZE,
    historyFilters,
    historyStats,
    historyKeepDays,
    loadHistoryStats,
    updateRunMeta,
    setHistoryPage,
    setHistoryFilters,
    // 动作
    loadStatus,
    submit,
    submitBatch,
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
