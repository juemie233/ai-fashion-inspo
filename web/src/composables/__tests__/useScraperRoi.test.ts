/**
 * 采集 ROI composable（useScraperRoi）测试。
 *
 * 关注：① 请求按当前窗口天数发起、切换维度不重复请求；② 展示格式化对「没有这个口径」
 * 与「样本为 0」都返回「—」而不是 0；③ 两个维度都空时 hasRows=false；④ 口径说明透传。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { formatCount, formatRate, useScraperRoi, type CollectionRoi } from '../useScraperRoi'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

function makeRoi(over: Partial<CollectionRoi> = {}): CollectionRoi {
  return {
    days: 180,
    by_keyword: [
      {
        keyword: 'JK制服',
        tasks: 7,
        found: 512,
        added: 301,
        add_rate: 58.8,
        attributed: 0,
        approved: 0,
        rejected: 0,
        pending: 0,
        approved_rate: null,
      },
    ],
    by_author: [
      {
        name: '里香',
        channel: 'f2',
        platform: 'douyin',
        tasks: null,
        found: null,
        added: null,
        add_rate: null,
        imported: 512,
        approved: 300,
        rejected: 12,
        pending: 200,
        approved_rate: 96.2,
      },
    ],
    coverage: {
      tasks_scanned: 47,
      multi_keyword_tasks: 0,
      keyword_attributed: 0,
      f2_materials: 512,
      notes: ['关键词的合格率暂无样本：只有入库时写入 scraper_task_id 的素材能按任务归属'],
    },
    ...over,
  }
}

describe('useScraperRoi', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.get.mockResolvedValue({ data: makeRoi() })
  })

  it('按当前天数请求聚合接口', async () => {
    const roi = useScraperRoi()
    await roi.loadRoi()
    expect(mocks.get).toHaveBeenCalledWith('/scraper/collection-roi', {
      params: { days: 180 },
    })
    expect(roi.roi.value?.by_keyword[0].keyword).toBe('JK制服')
    expect(roi.hasRows.value).toBe(true)
  })

  it('切换窗口天数后按新天数重新请求', async () => {
    const roi = useScraperRoi()
    roi.days.value = 30
    await roi.loadRoi()
    expect(mocks.get).toHaveBeenCalledWith('/scraper/collection-roi', {
      params: { days: 30 },
    })
  })

  it('两个维度都空时 hasRows=false', async () => {
    mocks.get.mockResolvedValue({ data: makeRoi({ by_keyword: [], by_author: [] }) })
    const roi = useScraperRoi()
    await roi.loadRoi()
    expect(roi.hasRows.value).toBe(false)
  })

  it('请求失败不抛错，置空结果', async () => {
    mocks.get.mockRejectedValue(new Error('boom'))
    const roi = useScraperRoi()
    await roi.loadRoi()
    expect(roi.roi.value).toBeNull()
  })

  it('口径说明透传给面板', async () => {
    const roi = useScraperRoi()
    await roi.loadRoi()
    expect(roi.notes.value[0]).toContain('scraper_task_id')
  })
})

describe('ROI 展示格式化', () => {
  it('百分比：null（样本为 0）显示 —，不用 0% 冒充结论', () => {
    expect(formatRate(null)).toBe('—')
    expect(formatRate(0)).toBe('0%')
    expect(formatRate(58.8)).toBe('58.8%')
  })

  it('计数：null（该通道没有这个口径）显示 —，0 仍显示 0', () => {
    expect(formatCount(null)).toBe('—')
    expect(formatCount(undefined)).toBe('—')
    expect(formatCount(0)).toBe('0')
    expect(formatCount(301)).toBe('301')
  })
})
