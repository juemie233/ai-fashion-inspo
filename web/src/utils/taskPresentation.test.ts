/**
 * taskPresentation 纯函数单测：任务结果文案汇总与两类任务归一化。
 *
 * 这些逻辑此前内联在 useTaskCenter 中，只能通过 mock axios + 实例化 composable
 * 间接覆盖；抽出为纯函数后，直接给原始对象断言即可，无需任何 mock。
 */

import { describe, expect, it } from 'vitest'
import {
  formatKeywords,
  normalizeQueueTask,
  normalizeScraperTask,
  parseMaxCount,
  summarizeResult,
  type QueueTask,
  type ScraperTaskRaw,
} from './taskPresentation'

function makeQueueTask(over: Partial<QueueTask> = {}): QueueTask {
  return {
    id: 5,
    type: 'batch_analyze',
    status: 'pending',
    progress: 0,
    total: 10,
    done: 0,
    result: null,
    error: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

function makeScraperTask(over: Partial<ScraperTaskRaw> = {}): ScraperTaskRaw {
  return {
    id: 3,
    platform: 'xiaohongshu',
    status: 'pending',
    config: null,
    items_found: 0,
    items_added: 0,
    error: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

describe('summarizeResult', () => {
  it('错误优先于 result 展示', () => {
    expect(summarizeResult('batch_analyze', { done: 5 }, '失败了')).toBe('失败了')
  })

  it('空/非对象 result 返回空串', () => {
    expect(summarizeResult('batch_analyze', null, null)).toBe('')
  })

  it('deduplicate 拼接删除数/释放空间/处理组数', () => {
    const text = summarizeResult(
      'deduplicate',
      { files_deleted: 4, freed_bytes: 2048, groups_processed: 3 },
      null,
    )
    expect(text).toContain('删除 4 个文件')
    expect(text).toContain('处理 3 组')
    // 2048 字节经 formatSize 渲染
    expect(text).toMatch(/释放\s/)
  })

  it('batch_delete 拼接删除数与释放空间', () => {
    expect(summarizeResult('batch_delete', { deleted_count: 2, freed_bytes: 0 }, null)).toBe(
      '删除 2 个素材 · 释放 0 B',
    )
  })

  it('quality_check 拼接各计数，缺失字段跳过', () => {
    const text = summarizeResult('quality_check', { approved: 10, rejected: 2, failed: 1 }, null)
    expect(text).toContain('通过 10')
    expect(text).toContain('拒绝 2')
    expect(text).toContain('失败 1')
    expect(text).not.toContain('未判定')
  })

  it('未知类型返回空串', () => {
    expect(summarizeResult('some_new_type', { foo: 1 }, null)).toBe('')
  })
})

describe('normalizeQueueTask', () => {
  it('pending 任务不标记 finished_at，target 取 total', () => {
    const t = normalizeQueueTask(makeQueueTask({ status: 'pending' }))
    expect(t.source).toBe('queue')
    expect(t.finished_at).toBeNull()
    expect(t.target).toBe(10)
    expect(t.title).toBeTruthy()
  })

  it('success 任务 finished_at 取 updated_at，并汇总 result 文案', () => {
    const t = normalizeQueueTask(
      makeQueueTask({
        status: 'completed',
        result: { done: 8 },
        updated_at: '2026-02-02T00:00:00Z',
      }),
    )
    expect(t.status).toBe('success')
    expect(t.finished_at).toBe('2026-02-02T00:00:00Z')
    expect(t.detail).toBe('完成 8 张')
  })
})

describe('normalizeScraperTask', () => {
  it('解析关键词与 max_count，平台名转中文', () => {
    const t = normalizeScraperTask(
      makeScraperTask({
        platform: 'xiaohongshu',
        config: JSON.stringify({ keywords: ['JK', '穿搭'], max_count: 50 }),
        items_found: 50,
        items_added: 20,
      }),
    )
    expect(t.source).toBe('scraper')
    expect(t.type).toBe('scraper')
    expect(t.target).toBe(50)
    expect(t.total).toBe(50)
    expect(t.done).toBe(20)
    expect(t.title).toContain('采集')
    expect(t.detail).toContain('关键词：JK、穿搭')
  })

  it('脏 config 不抛错，max_count 回退 0、关键词为空', () => {
    const t = normalizeScraperTask(makeScraperTask({ config: '{not json' }))
    expect(t.target).toBe(0)
    expect(t.detail).toBe('')
  })

  it('错误信息进入 detail', () => {
    const t = normalizeScraperTask(makeScraperTask({ error: '风控拦截' }))
    expect(t.detail).toContain('风控拦截')
  })
})

describe('formatKeywords / parseMaxCount', () => {
  it('formatKeywords 为空时返回空串', () => {
    expect(formatKeywords(null)).toBe('')
    expect(formatKeywords('{}')).toBe('')
  })

  it('parseMaxCount 对非数字/缺失返回 0', () => {
    expect(parseMaxCount(null)).toBe(0)
    expect(parseMaxCount('{"max_count":"x"}')).toBe(0)
  })
})
