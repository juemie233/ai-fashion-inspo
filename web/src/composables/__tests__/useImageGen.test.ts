/** useImageGen 状态机单测：提交 → 候选勾选 → 确认入库 → 丢弃。
 *
 * 轮询交给 usePolling（另有其测试），这里 mock 掉，保证断言确定。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchStatus: vi.fn(),
  createRun: vi.fn(),
  fetchRun: vi.fn(),
  confirmImport: vi.fn(),
  cancelRun: vi.fn(),
  discardRun: vi.fn(),
  previewPrompt: vi.fn(),
}))

vi.mock('@/api/generate', () => mocks)
vi.mock('@/composables/usePolling', () => ({
  usePolling: () => ({ start: vi.fn(), stop: vi.fn() }),
}))

import { useImageGen } from '../useImageGen'

const RUN_DETAIL = {
  run: {
    id: 7,
    kind: 't2i',
    status: 'done',
    prompt: '米色针织',
    negative: null,
    params: { width: 1024, height: 1024, seeds: [11, 12] },
    model_key: 'z_image_int8',
    workflow_name: 'z_image_t2i',
    workflow_hash: 'abc',
    total: 2,
    done: 2,
    failed: 0,
    error: null,
    task_id: 3,
    batch_dir: 'generated/7',
    created_at: '2026-10-21T09:00:00',
  },
  items: [
    { filename: '01_seed-11.png', index: 1, seed: 11, size_bytes: 100, imported: false, url: 'u1' },
    { filename: '02_seed-12.png', index: 2, seed: 12, size_bytes: 100, imported: true, url: 'u2' },
  ],
}

describe('useImageGen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submit 成功后记录 runId 与 seed，并拉取详情', async () => {
    mocks.createRun.mockResolvedValue({
      run_id: 7,
      task_id: 3,
      seed: 4242,
      batch_dir: 'generated/7',
      total: 2,
    })
    mocks.fetchRun.mockResolvedValue(RUN_DETAIL)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)

    const gen = useImageGen()
    const id = await gen.submit({ prompt: '米色针织', count: 2 })

    expect(id).toBe(7)
    expect(gen.runId.value).toBe(7)
    expect(gen.lastSeed.value).toBe(4242)
    expect(gen.candidates.value).toHaveLength(2)
    expect(success).toHaveBeenCalled()
    success.mockRestore()
  })

  it('阶段文案覆盖「等待显存」与失败原因（前端要能解释在等什么）', async () => {
    mocks.fetchRun.mockResolvedValue({
      run: { ...RUN_DETAIL.run, status: 'waiting_vram' },
      items: [],
      // 等待秒数来自 task.result（runner 写的是任务侧，不是提交参数快照）
      task: {
        id: 3,
        status: 'running',
        progress: 0,
        result: { stage: 'waiting_vram', waited_seconds: 40, vram_free_gib: 2.3 },
      },
    })
    const gen = useImageGen()
    // 直接注入 detail 的路径：靠 submit 拉取
    mocks.createRun.mockResolvedValue({ run_id: 7, task_id: 3, seed: 1, batch_dir: 'x', total: 1 })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    await gen.submit({ prompt: 'x' })
    expect(gen.stageText.value).toContain('等待显存')
    expect(gen.stageText.value).toContain('40')
    expect(gen.stageText.value).toContain('2.3') // 顺带把空闲显存说出来

    mocks.fetchRun.mockResolvedValue({
      run: { ...RUN_DETAIL.run, status: 'failed', error: '显存不足：请关闭浏览器标签' },
      items: [],
    })
    await gen.submit({ prompt: 'x' })
    expect(gen.stageText.value).toContain('显存不足')
    success.mockRestore()
  })

  it('toggle / selectAll 只勾未入库的候选', async () => {
    mocks.createRun.mockResolvedValue({ run_id: 7, task_id: 3, seed: 1, batch_dir: 'x', total: 2 })
    mocks.fetchRun.mockResolvedValue(RUN_DETAIL)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)

    const gen = useImageGen()
    await gen.submit({ prompt: 'x' })

    gen.toggle('01_seed-11.png')
    expect(gen.selected.value.has('01_seed-11.png')).toBe(true)
    gen.toggle('01_seed-11.png')
    expect(gen.selected.value.size).toBe(0)

    gen.selectAll()
    // 02 已入库 → 不该被选中
    expect([...gen.selected.value]).toEqual(['01_seed-11.png'])
    success.mockRestore()
  })

  it('confirm 把选中的文件名交给后端，并刷新候选状态', async () => {
    mocks.createRun.mockResolvedValue({ run_id: 7, task_id: 3, seed: 1, batch_dir: 'x', total: 2 })
    mocks.fetchRun.mockResolvedValue(RUN_DETAIL)
    mocks.confirmImport.mockResolvedValue({ imported: 1, skipped: 0, ids: ['insp-1'] })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)

    const gen = useImageGen()
    await gen.submit({ prompt: 'x' })
    gen.toggle('01_seed-11.png')

    const imported = await gen.confirm()

    expect(imported).toBe(1)
    expect(mocks.confirmImport).toHaveBeenCalledWith(7, ['01_seed-11.png'])
    expect(success).toHaveBeenCalledWith(expect.stringContaining('已入库 1 张'))
    success.mockRestore()
  })

  it('confirm 在没选中时直接返回 0（不打扰后端）', async () => {
    const gen = useImageGen()
    expect(await gen.confirm()).toBe(0)
    expect(mocks.confirmImport).not.toHaveBeenCalled()
  })

  it('discard 清空当前批次状态', async () => {
    mocks.createRun.mockResolvedValue({ run_id: 7, task_id: 3, seed: 1, batch_dir: 'x', total: 2 })
    mocks.fetchRun.mockResolvedValue(RUN_DETAIL)
    mocks.discardRun.mockResolvedValue({ removed_files: 2 })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const info = vi.spyOn(Message, 'info').mockImplementation((() => {}) as never)

    const gen = useImageGen()
    await gen.submit({ prompt: 'x' })
    await gen.discard()

    expect(mocks.discardRun).toHaveBeenCalledWith(7)
    expect(gen.run.value).toBeNull()
    expect(gen.runId.value).toBeNull()
    success.mockRestore()
    info.mockRestore()
  })
})
