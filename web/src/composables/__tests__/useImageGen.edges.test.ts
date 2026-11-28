/**
 * 本地出图状态机（useImageGen）**补齐用例**：既有两个测试文件覆盖提交→候选→入库主链路与
 * 历史/取消/重跑，本文件补：
 *
 * - 多姿态组 id 生成（crypto.randomUUID / getRandomValues / 时间戳兜底三条路）；
 * - 多姿态合并状态的合成（全取消 / 全失败 / 失败+取消混合 / 有活动）与阶段文案；
 * - 档位目录缓存读写边界（空对象/数组/坏 JSON/字段缺失/写失败）；
 * - 详情请求序号：reset 后在途响应不能复活旧批次；
 * - 轮询连续失败 5 次才停止；
 * - 各动作的「进行中」保护、提交失败、入库零结果提示、取消后停轮询、丢弃边界；
 * - 历史列表并发加载时旧响应不覆盖新响应；
 * - **近期新分支：多姿态合并视图下 regenerate 把新批次并入原分组（group_id 透传）**。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  cancelRun: vi.fn(),
  confirmImport: vi.fn(),
  createRun: vi.fn(),
  discardRun: vi.fn(),
  fetchRun: vi.fn(),
  fetchStatus: vi.fn(),
  listRuns: vi.fn(),
  previewPrompt: vi.fn(),
  regenerateCandidate: vi.fn(),
  retryRun: vi.fn(),
  fetchRunStats: vi.fn(),
  updateRunMetaApi: vi.fn(),
  polling: { start: vi.fn(), stop: vi.fn(), callback: null as null | (() => Promise<void>) },
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/generate', () => ({
  cancelRun: mocks.cancelRun,
  confirmImport: mocks.confirmImport,
  createRun: mocks.createRun,
  discardRun: mocks.discardRun,
  fetchRun: mocks.fetchRun,
  fetchStatus: mocks.fetchStatus,
  listRuns: mocks.listRuns,
  previewPrompt: mocks.previewPrompt,
  regenerateCandidate: mocks.regenerateCandidate,
  retryRun: mocks.retryRun,
  fetchRunStats: mocks.fetchRunStats,
  updateRunMeta: mocks.updateRunMetaApi,
}))

vi.mock('@/composables/usePolling', () => ({
  usePolling: (opts: { callback: () => Promise<void> }) => {
    mocks.polling.callback = opts.callback
    return { start: mocks.polling.start, stop: mocks.polling.stop }
  },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { readCachedModelsMeta, useImageGen, writeCachedModelsMeta } from '../useImageGen'

function detail(
  runOverrides: Record<string, unknown> = {},
  items: unknown[] = [],
  task: unknown = null,
) {
  return {
    run: {
      id: 1,
      kind: 't2i',
      status: 'running',
      prompt: '米色针织',
      negative: null,
      params: { width: 1024, height: 1024 },
      model_key: 'z_image_int8',
      workflow_name: 'z_image_t2i',
      total: 4,
      done: 0,
      failed: 0,
      error: null,
      task_id: 3,
      batch_dir: 'generated/1',
      created_at: '2026-01-01T00:00:00Z',
      ...runOverrides,
    },
    items,
    task,
  }
}

function candidate(filename: string, over: Record<string, unknown> = {}) {
  return {
    filename,
    index: 1,
    seed: 11,
    size_bytes: 100,
    imported: false,
    url: `u/${filename}`,
    ...over,
  }
}

async function flush() {
  for (let index = 0; index < 10; index += 1) await Promise.resolve()
}

/** 通过 submit 把单批详情写到内部状态（detail 不对外暴露） */
async function submitWith(
  gen: ReturnType<typeof useImageGen>,
  runOverrides: Record<string, unknown>,
  items: unknown[] = [],
  task: unknown = null,
) {
  const runId = Number(runOverrides.id ?? 1)
  mocks.createRun.mockResolvedValueOnce({
    run_id: runId,
    task_id: 3,
    seed: 1,
    batch_dir: '',
    total: 1,
  })
  mocks.fetchRun.mockResolvedValueOnce(detail(runOverrides, items, task))
  await gen.submit({ prompt: 'x' })
  await flush()
}

/** 提交两个姿态批次，让 fetchRun 回显 group_id；返回 generate 的 group_id */
async function submitTwoPoseGroup(
  gen: ReturnType<typeof useImageGen>,
  runOverrides: Array<Record<string, unknown>> = [{}, {}],
) {
  let created = 0
  const payloadById = new Map<number, { group_id?: string }>()
  mocks.createRun.mockImplementation(async (payload: { prompt: string; group_id?: string }) => {
    created += 1
    payloadById.set(created, payload)
    return { run_id: created, task_id: created, seed: 1, batch_dir: '', total: 1 }
  })
  mocks.fetchRun.mockImplementation(async (id: number) =>
    detail(
      { id, status: 'done', ...runOverrides[id - 1], group_id: payloadById.get(id)?.group_id },
      [candidate(`batch-${id}.png`)],
    ),
  )
  await gen.submitBatch([{ prompt: '姿势 A' }, { prompt: '姿势 B' }])
  await flush()
  return payloadById.get(1)?.group_id
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.values(mocks.message).forEach((fn) => fn.mockReset())
  mocks.polling.callback = null
  localStorage.clear()
  mocks.fetchStatus.mockResolvedValue({
    ready: true,
    reason: null,
    comfyui: {},
    models: {},
    ollama_resident: [],
    models_meta: {},
  })
  mocks.createRun.mockResolvedValue({ run_id: 1, task_id: 1, seed: 11, batch_dir: '', total: 1 })
  mocks.fetchRun.mockResolvedValue(detail())
  mocks.confirmImport.mockResolvedValue({ imported: 1, skipped: 0, ids: ['i1'] })
  mocks.cancelRun.mockResolvedValue({})
  mocks.discardRun.mockResolvedValue({ removed_files: 1 })
  mocks.listRuns.mockResolvedValue({ items: [], total: 0, keep_days: 7 })
  mocks.retryRun.mockResolvedValue({ run_id: 9, seed: 42 })
  mocks.regenerateCandidate.mockResolvedValue({ run_id: 33, seed: 5, original_seed: 11 })
  mocks.fetchRunStats.mockResolvedValue({ runs: 1 })
  mocks.updateRunMetaApi.mockResolvedValue({ id: 1, favorite: false })
})

describe('多姿态组 id 生成', () => {
  it('无 randomUUID 但有 getRandomValues：用随机字节生成 32 位小写十六进制', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0xab)
        return bytes
      },
    })
    const gen = useImageGen()

    await gen.submitBatch([{ prompt: '姿势 A' }, { prompt: '姿势 B' }])

    const payloads = mocks.createRun.mock.calls.map((call) => call[0] as { group_id?: string })
    expect(payloads[0]?.group_id).toBe('ab'.repeat(16))
    expect(payloads[1]?.group_id).toBe(payloads[0]?.group_id)
    vi.unstubAllGlobals()
  })

  it('无任何 crypto 能力：时间戳+随机数兜底仍是 32 位十六进制', async () => {
    vi.stubGlobal('crypto', {})
    const gen = useImageGen()

    await gen.submitBatch([{ prompt: '姿势 A' }, { prompt: '姿势 B' }])

    const payloads = mocks.createRun.mock.calls.map((call) => call[0] as { group_id?: string })
    expect(payloads[0]?.group_id).toMatch(/^[0-9a-f]{32}$/)
    vi.unstubAllGlobals()
  })

  it('单批次提交不带 group_id', async () => {
    const gen = useImageGen()

    await gen.submitBatch([{ prompt: '单张' }])

    expect(mocks.createRun.mock.calls[0]?.[0]).not.toHaveProperty('group_id')
  })
})

describe('多姿态合并状态与阶段文案', () => {
  it('无批次时 run 为 null、stageText 为空串', () => {
    const gen = useImageGen()
    expect(gen.run.value).toBeNull()
    expect(gen.stageText.value).toBe('')
  })

  it('有分组 id 但没有批次详情：run 为 null，阶段文案为空串（读取中分支当前不可达）', () => {
    const gen = useImageGen()
    gen.groupRunIds.value = [1, 2]
    expect(gen.run.value).toBeNull()
    // stageText 先判 run.value 为空直接返回 ''，`正在读取多姿态结果…` 分支实际不可达
    expect(gen.stageText.value).toBe('')
  })

  it('合并状态：有活动优先；全取消=cancelled；全失败/失败+取消=failed', async () => {
    const gen = useImageGen()

    await submitTwoPoseGroup(gen, [{ status: 'done' }, { status: 'running' }])
    expect(gen.run.value?.status).toBe('running')

    const gen2 = useImageGen()
    await submitTwoPoseGroup(gen2, [{ status: 'cancelled' }, { status: 'cancelled' }])
    expect(gen2.run.value?.status).toBe('cancelled')

    const gen3 = useImageGen()
    await submitTwoPoseGroup(gen3, [{ status: 'failed' }, { status: 'failed' }])
    expect(gen3.run.value?.status).toBe('failed')

    const gen4 = useImageGen()
    await submitTwoPoseGroup(gen4, [{ status: 'failed' }, { status: 'cancelled' }])
    expect(gen4.run.value?.status).toBe('failed')
  })

  it('阶段文案：生成中 / 全部失败 / 全部完成 / 部分失败', async () => {
    const generating = useImageGen()
    await submitTwoPoseGroup(generating, [
      { status: 'done', done: 2, total: 2, failed: 0 },
      { status: 'running', done: 0, total: 2, failed: 0 },
    ])
    expect(generating.stageText.value).toContain('个姿态仍在生成')

    const allFailed = useImageGen()
    await submitTwoPoseGroup(allFailed, [
      { status: 'failed', error: '显存不足', done: 0, total: 2, failed: 2 },
      { status: 'failed', done: 0, total: 2, failed: 2 },
    ])
    expect(allFailed.stageText.value).toContain('全部 2 个姿态生成失败')

    const allDone = useImageGen()
    await submitTwoPoseGroup(allDone, [
      { status: 'done', done: 2, total: 2, failed: 0 },
      { status: 'done', done: 2, total: 2, failed: 0 },
    ])
    expect(allDone.stageText.value).toContain('勾选后确认入库')

    const partlyFailed = useImageGen()
    await submitTwoPoseGroup(partlyFailed, [
      { status: 'done', done: 2, total: 2, failed: 0 },
      { status: 'failed', done: 0, total: 2, failed: 2 },
    ])
    expect(partlyFailed.stageText.value).toContain('多姿态生成结束：成功 2 张')
  })

  it('单批次阶段文案覆盖 waiting_vram / queued / done / cancelled / failed / 未知状态', async () => {
    const gen = useImageGen()

    await submitWith(gen, { status: 'waiting_vram' }, [], {
      result: { stage: 'waiting', waited_seconds: 12, vram_free_gib: 3.5 },
    })
    expect(gen.stageText.value).toBe(
      '等待显存（已等 12 秒，当前空闲 3.5 GiB）：Ollama 正占着 GPU，可执行 ollama stop 或稍候',
    )

    await submitWith(gen, { status: 'queued', id: 2 })
    expect(gen.stageText.value).toBe('排队中（等待 worker 认领）')

    await submitWith(gen, { status: 'done', done: 4, total: 4, id: 3 })
    expect(gen.stageText.value).toBe('已完成 4 张，勾选后确认入库')

    await submitWith(gen, { status: 'cancelled', id: 4 })
    expect(gen.stageText.value).toBe('已取消')

    await submitWith(gen, { status: 'failed', error: 'OOM', id: 5 })
    expect(gen.stageText.value).toBe('OOM')

    await submitWith(gen, { status: 'weird_status', id: 6 })
    expect(gen.stageText.value).toBe('weird_status')
  })
})

describe('档位目录缓存', () => {
  it('无缓存/空对象/数组/坏 JSON/字段缺失都返回 null', () => {
    expect(readCachedModelsMeta()).toBeNull()

    localStorage.setItem('fashion-inspo:generate:models-meta:v1', '{}')
    expect(readCachedModelsMeta()).toBeNull()

    localStorage.setItem('fashion-inspo:generate:models-meta:v1', '[]')
    expect(readCachedModelsMeta()).toBeNull()

    localStorage.setItem('fashion-inspo:generate:models-meta:v1', '{bad json')
    expect(readCachedModelsMeta()).toBeNull()

    localStorage.setItem(
      'fashion-inspo:generate:models-meta:v1',
      JSON.stringify({ a: { label: '档位A' } }), // 缺 kind
    )
    expect(readCachedModelsMeta()).toBeNull()
  })

  it('形状完整的缓存原样读出', () => {
    const meta = { a: { label: '档位A', kind: 't2i' } }
    localStorage.setItem('fashion-inspo:generate:models-meta:v1', JSON.stringify(meta))
    expect(readCachedModelsMeta()).toEqual(meta)
  })

  it('写缓存：空元数据不写；写失败静默不抛错', () => {
    writeCachedModelsMeta(undefined)
    writeCachedModelsMeta({})
    expect(localStorage.getItem('fashion-inspo:generate:models-meta:v1')).toBeNull()

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() =>
      writeCachedModelsMeta({ a: { label: 'A', kind: 't2i', defaults: {} } }),
    ).not.toThrow()
    setItem.mockRestore()
  })
})

describe('详情竞态与轮询失败', () => {
  it('reset 之后在途的详情响应不能复活旧批次', async () => {
    mocks.createRun.mockResolvedValueOnce({
      run_id: 7,
      task_id: 3,
      seed: 1,
      batch_dir: '',
      total: 1,
    })
    let resolveDetail!: (value: unknown) => void
    mocks.fetchRun.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveDetail = resolve
        }),
    )
    const gen = useImageGen()

    const submitting = gen.submit({ prompt: '旧批次' })
    await flush()
    gen.reset()
    resolveDetail(detail({ id: 7, status: 'done' }))
    await submitting

    expect(gen.run.value).toBeNull()
    expect(gen.runId.value).toBeNull()
  })

  it('轮询连续失败 5 次才停止并提示一次', async () => {
    const gen = useImageGen()
    expect(mocks.polling.callback).not.toBeNull()
    // 同步抛错才能让 refreshCurrentDetails 整体抛出（allSettled 会吞掉 rejected promise）
    mocks.fetchRun.mockImplementation(() => {
      throw new Error('sync network')
    })
    gen.runId.value = 7

    for (let index = 0; index < 5; index += 1) await mocks.polling.callback!()

    expect(mocks.polling.stop).toHaveBeenCalled()
    expect(mocks.message.error).toHaveBeenCalledWith('获取生成状态多次失败，已停止轮询，请稍后刷新')
  })

  it('没有当前批次时轮询回调直接返回（不发详情请求）', async () => {
    useImageGen()
    mocks.fetchRun.mockClear()

    await mocks.polling.callback!()

    expect(mocks.fetchRun).not.toHaveBeenCalled()
  })
})

describe('动作的进行中保护与失败提示', () => {
  it('submitting=true 时 submit / submitBatch / retry / retryFailedBatches / regenerate 都不发请求', async () => {
    const gen = useImageGen()
    gen.submitting.value = true

    await expect(gen.submit({ prompt: 'x' })).resolves.toBeNull()
    await expect(gen.submitBatch([{ prompt: 'x' }])).resolves.toEqual({
      succeeded: 0,
      failed: 0,
      failures: [],
    })
    await expect(gen.retry(1)).resolves.toBeNull()
    await expect(gen.retryFailedBatches()).resolves.toEqual({
      succeeded: 0,
      failed: 0,
      failures: [],
    })
    await expect(gen.regenerate(candidate('a.png') as never)).resolves.toBeNull()

    expect(mocks.createRun).not.toHaveBeenCalled()
    expect(mocks.retryRun).not.toHaveBeenCalled()
    expect(mocks.regenerateCandidate).not.toHaveBeenCalled()
  })

  it('submit 失败：提示「提交出图任务失败」并返回 null', async () => {
    mocks.createRun.mockRejectedValueOnce({ response: { data: {} } })
    const gen = useImageGen()

    await expect(gen.submit({ prompt: 'x' })).resolves.toBeNull()

    expect(mocks.message.error).toHaveBeenCalledWith('提交出图任务失败')
    expect(gen.submitting.value).toBe(false)
  })

  it('confirm：选中图都已存在→info；没有可入库图→info', async () => {
    const gen = useImageGen()
    await submitWith(gen, { status: 'done' }, [candidate('a.png', { imported: true })])
    gen.selected.value = new Set(['a.png'])
    mocks.confirmImport.mockResolvedValueOnce({ imported: 0, skipped: 1, ids: [] })

    await expect(gen.confirm()).resolves.toBe(0)
    expect(mocks.message.info).toHaveBeenCalledWith('选中的 1 张都已在库中')

    mocks.confirmImport.mockResolvedValueOnce({ imported: 0, skipped: 0, ids: [] })
    gen.selected.value = new Set(['a.png'])
    await gen.confirm()
    expect(mocks.message.info).toHaveBeenCalledWith('没有可入库的图（可能候选文件已失效）')
  })

  it('取消：批次已终态时停止轮询（不再续排）', async () => {
    const gen = useImageGen()
    await submitWith(gen, { status: 'done' })
    mocks.polling.start.mockClear()
    mocks.polling.stop.mockClear()

    await gen.cancel()

    expect(mocks.cancelRun).toHaveBeenCalledWith(1)
    expect(mocks.polling.stop).toHaveBeenCalled()
    expect(mocks.polling.start).not.toHaveBeenCalled()
  })

  it('丢弃：没有当前批次不发请求；失败提示「丢弃失败」', async () => {
    const gen = useImageGen()
    await gen.discard()
    expect(mocks.discardRun).not.toHaveBeenCalled()

    gen.runId.value = 7
    mocks.discardRun.mockRejectedValueOnce({ response: { data: {} } })
    await gen.discard()
    expect(mocks.message.error).toHaveBeenCalledWith('丢弃失败')
  })
})

describe('历史列表并发加载', () => {
  it('旧响应不能覆盖新响应', async () => {
    let resolveOld!: (value: unknown) => void
    mocks.listRuns
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          }),
      )
      .mockResolvedValueOnce({ items: [{ id: 2 }], total: 1, keep_days: 7 })
    const gen = useImageGen()

    const oldLoad = gen.loadHistory()
    await gen.loadHistory()
    expect(gen.history.value).toEqual([{ id: 2 }])

    resolveOld({ items: [{ id: 1 }], total: 9, keep_days: 7 })
    await oldLoad
    expect(gen.history.value).toEqual([{ id: 2 }])
    expect(gen.historyTotal.value).toBe(1)
  })
})

describe('regenerate：多姿态新批次归入原分组（近期新分支）', () => {
  it('regenerate 透传当前组 group_id，新批次追加进 groupRunIds 而不是切走整组', async () => {
    const gen = useImageGen()
    const groupId = await submitTwoPoseGroup(gen)
    expect(groupId).toMatch(/^[0-9a-f]{32}$/)
    const firstCandidate = gen.candidates.value[0]!
    expect(firstCandidate.run_id).toBe(1)

    const newRunId = await gen.regenerate(firstCandidate as never)

    expect(newRunId).toBe(33)
    expect(mocks.regenerateCandidate).toHaveBeenCalledWith(1, firstCandidate.filename, 0, groupId)
    expect(gen.groupRunIds.value).toEqual([1, 2, 33])
    expect(gen.runId.value).toBe(33)
    expect(mocks.message.success).toHaveBeenCalledWith('已用新 seed 5 重出（原 seed 11）')
  })

  it('组内最后一批没有 group_id 时回退到组内其它批次的 group_id', async () => {
    const gen = useImageGen()
    let created = 0
    mocks.createRun.mockImplementation(async () => {
      created += 1
      return { run_id: created, task_id: created, seed: 1, batch_dir: '', total: 1 }
    })
    // 只有第一批带 group_id：合成 run 取最后一批 → 需要回退查找
    mocks.fetchRun.mockImplementation(async (id: number) =>
      detail({ id, status: 'done', group_id: id === 1 ? 'grp-fallback' : undefined }),
    )
    await gen.submitBatch([{ prompt: '姿势 A' }, { prompt: '姿势 B' }])
    await flush()

    await gen.regenerate({ ...candidate('a.png'), run_id: 2 } as never)

    expect(mocks.regenerateCandidate).toHaveBeenCalledWith(2, 'a.png', 0, 'grp-fallback')
    expect(gen.groupRunIds.value).toEqual([1, 2, 33])
  })

  it('单批次 regenerate：不传 group_id，切到新批次', async () => {
    const gen = useImageGen()
    await submitWith(gen, { status: 'done' }, [candidate('a.png')])

    await gen.regenerate({ ...candidate('a.png'), run_id: 1 } as never)

    expect(mocks.regenerateCandidate).toHaveBeenCalledWith(1, 'a.png', 0, undefined)
    expect(gen.groupRunIds.value).toEqual([])
    expect(gen.runId.value).toBe(33)
  })

  it('没有可用的源批次：直接返回 null，不发请求', async () => {
    const gen = useImageGen()

    await expect(gen.regenerate(candidate('a.png') as never)).resolves.toBeNull()

    expect(mocks.regenerateCandidate).not.toHaveBeenCalled()
  })

  it('regenerate 失败：提示「重出失败」并返回 null', async () => {
    const gen = useImageGen()
    await submitWith(gen, { status: 'done' }, [candidate('a.png')])
    mocks.regenerateCandidate.mockRejectedValueOnce({ response: { data: {} } })

    await expect(gen.regenerate({ ...candidate('a.png'), run_id: 1 } as never)).resolves.toBeNull()

    expect(mocks.message.error).toHaveBeenCalledWith('重出失败')
    expect(gen.submitting.value).toBe(false)
  })
})
