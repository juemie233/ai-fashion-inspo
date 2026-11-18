/**
 * 任务中心对「博主评分补全」类型的最小回归：标题不能显示英文 type，
 * 运行中/完成后的说明要写成「已补 X 个素材」而不是抽象的 N/N。
 */

import { describe, expect, it } from 'vitest'

import {
  describeRunningTask,
  isCancelableTaskType,
  normalizeQueueTask,
  summarizeResult,
} from '../taskPresentation'
import { TASK_TYPE_LABELS } from '../taskLabel'

/** 造一条任务队列原始条目（只填本用例需要的字段） */
function queueTask(overrides: Record<string, unknown> = {}) {
  return {
    id: 88,
    type: 'blogger_rating_backfill',
    status: 'running',
    progress: 30,
    total: 10,
    done: 3,
    result: null,
    error: null,
    created_at: '2026-11-18T10:00:00Z',
    updated_at: '2026-11-18T10:00:10Z',
    ...overrides,
  } as never
}

describe('博主评分补全的任务展示', () => {
  it('类型标题有中文名，不直接显示 english type', () => {
    expect(TASK_TYPE_LABELS.blogger_rating_backfill).toBe('博主素材评分补全')
    expect(normalizeQueueTask(queueTask()).title).toBe('博主素材评分补全')
  })

  it('任务运行中可取消（与后端任务白名单同步）', () => {
    expect(isCancelableTaskType('blogger_rating_backfill')).toBe(true)
  })

  it('运行中说明进度含义：已补 done/total 个素材', () => {
    expect(normalizeQueueTask(queueTask()).detail).toBe('已补 3/10 个素材')
  })

  it('排队中提示等待 worker 认领', () => {
    expect(describeRunningTask('blogger_rating_backfill', null, 'pending', 0, 10)).toBe(
      '排队中：等待 worker 认领',
    )
  })

  it('完成后汇总结果：已补素材数 / 跳过 / 失败', () => {
    expect(
      summarizeResult(
        'blogger_rating_backfill',
        { updated_materials: 8, skipped: 1, failed: 1 },
        null,
      ),
    ).toBe('已补 8 个素材 · 跳过 1 位博主 · 失败 1 位博主')
    // 旧任务字段兼容
    expect(summarizeResult('blogger_rating_backfill', { updated: 6 }, null)).toBe('已补 6 个素材')
    expect(summarizeResult('blogger_rating_backfill', { rated: 5 }, null)).toBe('已补 5 个素材')
  })

  it('没有可补素材时为空文案（不显示 0 个素材）', () => {
    expect(summarizeResult('blogger_rating_backfill', { updated: 0 }, null)).toBe('')
  })

  it('任务成功后任务中心列表的 detail 是汇总而非运行中文案', () => {
    const task = normalizeQueueTask(
      queueTask({
        status: 'success',
        progress: 100,
        done: 10,
        result: { updated_materials: 9, skipped: 1 },
      }),
    )
    expect(task.detail).toBe('已补 9 个素材 · 跳过 1 位博主')
  })
})
