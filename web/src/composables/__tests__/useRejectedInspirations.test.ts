/**
 * 质量审核「未通过素材」列表（`useRejectedInspirations`）的测试。
 *
 * 这里最容易出问题的是**分页与竞态**：
 * - 切筛选后必须回到第 1 页（否则停在第 3 页的筛选结果可能为空，用户以为没素材）；
 * - 加载更多必须追加而不是覆盖，且加载中重复点击不能跳页；
 * - 旧请求晚到要丢弃（切筛选后旧结果不能盖住新筛选结果）；
 * - 单张通过后要从列表移除并同步 total（否则总数与可见行对不上）。
 * 另外动作方法必须把参数原样透传给对应接口（原因/来源口径错了会污染垃圾桶统计）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  submitCorrection: vi.fn(),
  batchTrash: vi.fn(),
  batchUpdate: vi.fn(),
  deleteRejected: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: { get: mocks.get, patch: mocks.patch } }))
vi.mock('@/api/aiQuality', () => ({ submitQualityCorrection: mocks.submitCorrection }))
vi.mock('@/api/inspirations', () => ({
  batchTrash: mocks.batchTrash,
  batchUpdateInspirations: mocks.batchUpdate,
  deleteRejectedInspirations: mocks.deleteRejected,
}))

import { useRejectedInspirations, type RejectedItem } from '../useRejectedInspirations'

function item(id: string, overrides: Partial<RejectedItem> = {}): RejectedItem {
  return {
    id,
    file_path: `images/${id}.webp`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    quality_reason: 'AI 判定',
    source_type: 'manual_upload',
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function payload(items: RejectedItem[], total = items.length) {
  return { data: { items, total } }
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve()
}

describe('useRejectedInspirations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue(payload([item('i1'), item('i2')], 120))
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.deleteRejected.mockResolvedValue({ trashed: 5, message: '已移入垃圾桶 5 条' })
    mocks.batchUpdate.mockResolvedValue(2)
    mocks.batchTrash.mockResolvedValue({ trashed: 2, skipped: 0 })
    mocks.submitCorrection.mockResolvedValue({ id: 1 })
  })

  describe('加载', () => {
    it('默认按「rejected + 最新优先 + 第 1 页 + size=100」拉取', async () => {
      const rejected = useRejectedInspirations()

      await rejected.load()

      expect(mocks.get).toHaveBeenCalledWith('/inspirations', {
        params: {
          quality_status: 'rejected',
          size: 100,
          page: 1,
          sort: 'newest',
        },
      })
      expect(rejected.items.value.map((i) => i.id)).toEqual(['i1', 'i2'])
      expect(rejected.total.value).toBe(120)
      expect(rejected.page.value).toBe(1)
      expect(rejected.loading.value).toBe(false)
    })

    it('选择原因筛选时追加 quality_reason_code，并回到第 1 页', async () => {
      const rejected = useRejectedInspirations()
      rejected.page.value = 3
      rejected.reasonFilter.value = 'blurry'

      await rejected.load(true)

      expect(mocks.get).toHaveBeenCalledWith('/inspirations', {
        params: {
          quality_status: 'rejected',
          size: 100,
          page: 1,
          sort: 'newest',
          quality_reason_code: 'blurry',
        },
      })
      expect(rejected.page.value).toBe(1)
    })

    it('load(false) 保留当前页并追加结果（加载更多）', async () => {
      const rejected = useRejectedInspirations()
      await rejected.load()
      rejected.page.value = 2
      mocks.get.mockResolvedValueOnce(payload([item('i3')], 120))

      await rejected.load(false)

      expect(mocks.get).toHaveBeenLastCalledWith(
        '/inspirations',
        expect.objectContaining({ params: expect.objectContaining({ page: 2 }) }),
      )
      expect(rejected.items.value.map((i) => i.id)).toEqual(['i1', 'i2', 'i3'])
      expect(rejected.total.value).toBe(120)
    })

    it('加载失败静默：不抛错、loading 复位、列表保持原样', async () => {
      const rejected = useRejectedInspirations()
      rejected.items.value = [item('keep')]
      mocks.get.mockRejectedValue(new Error('网络错误'))

      await expect(rejected.load()).resolves.toBeUndefined()

      expect(rejected.items.value.map((i) => i.id)).toEqual(['keep'])
      expect(rejected.loading.value).toBe(false)
    })

    it('请求在途时 loading=true，结束后复位', async () => {
      const pending = deferred<{ data: { items: RejectedItem[]; total: number } }>()
      mocks.get.mockReturnValue(pending.promise)
      const rejected = useRejectedInspirations()

      const promise = rejected.load()
      expect(rejected.loading.value).toBe(true)

      pending.resolve(payload([item('i1')], 1))
      await promise

      expect(rejected.loading.value).toBe(false)
    })

    it('竞态：先发出的旧响应晚到会被丢弃', async () => {
      const slow = deferred<{ data: { items: RejectedItem[]; total: number } }>()
      const fast = deferred<{ data: { items: RejectedItem[]; total: number } }>()
      mocks.get.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
      const rejected = useRejectedInspirations()

      const oldCall = rejected.load()
      const newCall = rejected.load()

      fast.resolve(payload([item('new')], 1))
      await newCall
      expect(rejected.items.value.map((i) => i.id)).toEqual(['new'])

      slow.resolve(payload([item('stale')], 99))
      await oldCall

      expect(rejected.items.value.map((i) => i.id)).toEqual(['new'])
      expect(rejected.total.value).toBe(1)
      expect(rejected.loading.value).toBe(false)
    })
  })

  describe('分页 loadMore', () => {
    it('页码 +1 并拉取下一页', async () => {
      const rejected = useRejectedInspirations()
      await rejected.load()
      mocks.get.mockResolvedValueOnce(payload([item('i3')], 120))

      rejected.loadMore()
      await flush()

      expect(rejected.page.value).toBe(2)
      expect(mocks.get).toHaveBeenLastCalledWith(
        '/inspirations',
        expect.objectContaining({ params: expect.objectContaining({ page: 2 }) }),
      )
      expect(rejected.items.value).toHaveLength(3)
    })

    it('加载中重复触发不会跳页', async () => {
      const rejected = useRejectedInspirations()
      rejected.loading.value = true

      rejected.loadMore()
      rejected.loadMore()

      expect(rejected.page.value).toBe(1)
      expect(mocks.get).not.toHaveBeenCalled()
    })
  })

  describe('审核动作', () => {
    it('单张通过：PATCH 状态、从列表移除并同步 total', async () => {
      const rejected = useRejectedInspirations()
      await rejected.load()

      await rejected.approve('i1')

      expect(mocks.patch).toHaveBeenCalledWith('/inspirations/i1', { quality_status: 'approved' })
      expect(rejected.items.value.map((i) => i.id)).toEqual(['i2'])
      expect(rejected.total.value).toBe(119)
    })

    it('单张通过时 total 不会减成负数', async () => {
      const rejected = useRejectedInspirations()
      rejected.items.value = [item('i1')]
      rejected.total.value = 0

      await rejected.approve('i1')

      expect(rejected.total.value).toBe(0)
    })

    it('全部已拒绝移入垃圾桶：清空列表并返回后端结果', async () => {
      const rejected = useRejectedInspirations()
      await rejected.load()

      const result = await rejected.trashAllRejected()

      expect(mocks.deleteRejected).toHaveBeenCalledTimes(1)
      expect(result).toEqual({ trashed: 5, message: '已移入垃圾桶 5 条' })
      expect(rejected.items.value).toEqual([])
      expect(rejected.total.value).toBe(0)
    })

    it('批量通过透传 ids 与目标状态', async () => {
      const rejected = useRejectedInspirations()

      const result = await rejected.batchApprove(['a', 'b'])

      expect(mocks.batchUpdate).toHaveBeenCalledWith(['a', 'b'], { quality_status: 'approved' })
      expect(result).toBe(2)
    })

    it('批量移入垃圾桶固定用「质量差 + auto」口径', async () => {
      const rejected = useRejectedInspirations()

      const result = await rejected.batchTrashSelected(['a'])

      expect(mocks.batchTrash).toHaveBeenCalledWith(['a'], '质量差', 'auto')
      expect(result).toEqual({ trashed: 2, skipped: 0 })
    })

    it('误判反馈原样透传给接口', async () => {
      const rejected = useRejectedInspirations()
      const correction = {
        inspiration_id: 'i1',
        corrected_status: 'approved' as const,
        reason_code: 'blurry',
        note: '其实是虚焦艺术照',
      }

      await rejected.submitCorrection(correction)

      expect(mocks.submitCorrection).toHaveBeenCalledWith(correction)
    })
  })
})
