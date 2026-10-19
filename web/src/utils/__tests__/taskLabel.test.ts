/** `predictEta` 的两种口径：批量 AI 分析走「平均耗时 × 未完成数」，其余按实测速率外推。 */

import { describe, expect, it } from 'vitest'
import { overallAvgMs, type ModelStatItem } from '@/api/aiModelStats'
import { predictEta } from '@/utils/taskLabel'
import type { UnifiedTask } from '@/types/task'

function task(partial: Partial<UnifiedTask>): UnifiedTask {
  return {
    id: '1',
    source: 'queue',
    type: 'batch_analyze',
    status: 'running',
    progress: 0,
    done: 0,
    target: 0,
    created_at: new Date().toISOString(),
    started_at: new Date(Date.now() - 60_000).toISOString(),
    finished_at: null,
    error: null,
    detail: '',
    ...partial,
  } as UnifiedTask
}

describe('predictEta', () => {
  it('批量 AI 分析：平均耗时 × 未完成数（忽略视频多帧）', () => {
    // 100 条里完成 40 条 → 剩余 60 条；每条约 10 秒 → 600 秒 = 10 分钟
    const text = predictEta(task({ done: 40, target: 100 }), 10_000)
    expect(text).toBe('10 分钟')
  })

  it('拿不到平均耗时 → 回退到实测速率外推', () => {
    // 60 秒完成 30 条 → 2 秒/条；剩余 30 条 → 约 60 秒
    const text = predictEta(task({ done: 30, target: 60 }), null)
    expect(text).toBe('1 分钟')
  })

  it('已完成或没有目标数时不预测', () => {
    expect(predictEta(task({ done: 10, target: 10 }), 10_000)).toBeNull()
    expect(predictEta(task({ done: 0, target: 0 }), 10_000)).toBeNull()
  })

  it('采集任务仍按速率外推（不受平均耗时影响）', () => {
    const text = predictEta(task({ type: 'scraper', done: 30, target: 60 }), 10_000)
    expect(text).toBe('1 分钟')
  })
})

describe('overallAvgMs', () => {
  const base: ModelStatItem = {
    model_name: 'x',
    total_analyses: 10,
    success_count: 10,
    failure_count: 0,
    success_rate: 100,
    avg_time_ms: 1000,
    avg_tags: 3,
    last_used: '',
  }

  it('优先用「（全部模型汇总）」行', () => {
    const items = [
      { ...base, model_name: '（全部模型汇总）', avg_time_ms: 2500, total_analyses: 100 },
      { ...base, avg_time_ms: 9000 },
    ]
    expect(overallAvgMs(items)).toBe(2500)
  })

  it('没有汇总行时按分析次数加权', () => {
    const items = [
      { ...base, avg_time_ms: 1000, total_analyses: 10 },
      { ...base, avg_time_ms: 3000, total_analyses: 30 },
    ]
    expect(overallAvgMs(items)).toBe(2500)
  })

  it('没有可用数据时返回 null', () => {
    expect(overallAvgMs([])).toBeNull()
    expect(overallAvgMs([{ ...base, avg_time_ms: 0 }])).toBeNull()
  })
})
