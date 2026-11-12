/** 任务展示规则（P0-2）：阶段文案、结果摘要、时间与耗时格式化。 */

import {
  describeStage,
  formatRunDuration,
  formatRunTime,
  parseServerTime,
  runStatusLabel,
  stageLabel,
  summarizeTaskResult,
} from '../utils/taskPresentation'
import type { TaskItem, TaskRun } from '../services/api'

function task(over: Partial<TaskItem> = {}): TaskItem {
  return {
    id: 1,
    type: 'batch_analyze',
    status: 'running',
    progress: 0,
    total: 0,
    done: 0,
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    ...over,
  } as TaskItem
}

describe('describeStage', () => {
  it('排队中给出等待说明（f2 额外说明等执行器认领）', () => {
    expect(describeStage(task({ status: 'pending' }))).toBe('排队中：等待开始')
    expect(describeStage(task({ type: 'f2_import', status: 'pending' }))).toBe(
      '排队中：等待执行器认领',
    )
  })

  it('运行中按 result.stage 显示阶段 + 计数', () => {
    expect(
      describeStage(
        task({ type: 'f2_import', status: 'running', done: 4, total: 21, result: { stage: 'download' } }),
      ),
    ).toBe('下载中：第 4/21')
    expect(
      describeStage(
        task({ type: 'f2_import', status: 'running', done: 30, total: 120, result: { stage: 'import' } }),
      ),
    ).toBe('入库中：第 30/120')
  })

  it('运行中没有阶段标记时只说「处理中」并带计数', () => {
    expect(describeStage(task({ status: 'running', done: 3, total: 10 }))).toBe('处理中：第 3/10')
    expect(describeStage(task({ status: 'running' }))).toBe('处理中')
  })

  it('暂停给出可预期说明；已结束返回空串（走摘要）', () => {
    expect(describeStage(task({ status: 'paused' }))).toContain('已暂停')
    expect(describeStage(task({ status: 'success' }))).toBe('')
    expect(describeStage(task({ status: 'failed' }))).toBe('')
  })

  it('未知阶段回退原值', () => {
    expect(stageLabel('brand_new_stage')).toBe('brand_new_stage')
    expect(stageLabel(null)).toBe('')
  })
})

describe('summarizeTaskResult', () => {
  it('向量回填：列出图像/文本/失败/跳过', () => {
    expect(
      summarizeTaskResult(
        task({
          type: 'vector_backfill',
          status: 'success',
          result: { image_done: 100, text_done: 98, text_failed: 2, image_skipped: 3 },
        }),
      ),
    ).toBe('图像向量 100 · 文本向量 98 · 文本失败 2 · 跳过 3')
  })

  it('质量审核 / 去重 / 批量分析 / 删除各自给出业务数字', () => {
    expect(
      summarizeTaskResult(
        task({ type: 'quality_check', status: 'success', result: { approved: 9, rejected: 1 } }),
      ),
    ).toBe('通过 9 · 拒绝 1')
    expect(
      summarizeTaskResult(
        task({
          type: 'deduplicate',
          status: 'success',
          result: { files_deleted: 4, groups_processed: 2 },
        }),
      ),
    ).toBe('删除 4 个文件 · 处理 2 组')
    expect(
      summarizeTaskResult(task({ type: 'batch_analyze', status: 'success', result: { done: 36 } })),
    ).toBe('完成 36 张')
    expect(
      summarizeTaskResult(
        task({ type: 'batch_delete', status: 'success', result: { deleted_count: 7 } }),
      ),
    ).toBe('删除 7 个素材')
  })

  it('f2 入库 0 时明确说「本轮没有新素材」', () => {
    expect(
      summarizeTaskResult(
        task({ type: 'f2_import', status: 'success', result: { import: { imported: 0 } } }),
      ),
    ).toBe('入库 0 · 本轮没有新素材')
  })

  it('失败任务不显示摘要（错误由页面显示），未知类型/空结果返回空串', () => {
    expect(
      summarizeTaskResult(task({ type: 'vector_backfill', status: 'failed', result: { image_done: 1 } })),
    ).toBe('')
    expect(summarizeTaskResult(task({ type: 'image_gen', status: 'success', result: {} }))).toBe('')
    expect(summarizeTaskResult(task({ type: 'batch_analyze', status: 'success' }))).toBe('')
  })
})

describe('时间与耗时', () => {
  it('没有时区后缀的历史载荷按 UTC 解析（避免差 8 小时）', () => {
    const naive = parseServerTime('2026-10-24T00:00:00')
    const zulu = parseServerTime('2026-10-24T00:00:00Z')
    expect(naive?.toISOString()).toBe(zulu?.toISOString())
    expect(parseServerTime(null)).toBeNull()
    expect(parseServerTime('not-a-date')).toBeNull()
  })

  it('formatRunTime 输出本地月-日 时:分（同一年不带年份）', () => {
    const value = formatRunTime('2026-10-24T00:00:00Z', new Date('2026-10-25T00:00:00Z'))
    expect(value).toMatch(/^10-24 \d{2}:\d{2}$/)
    // 跨年：now 在 2027 年时，2026 年的时间要带年份
    const crossYear = formatRunTime('2025-12-31T23:00:00Z', new Date('2027-01-01T00:00:00Z'))
    expect(crossYear).toMatch(/^2026-01-01 \d{2}:\d{2}$/)
  })

  it('formatRunDuration 覆盖秒/分/小时', () => {
    const run = (start: string, end: string | null): TaskRun =>
      ({
        id: 1,
        task_id: 1,
        task_type: 'batch_analyze',
        attempt: 0,
        started_at: start,
        finished_at: end,
        item_count: 0,
        total_count: 0,
        status: 'success',
      }) as TaskRun
    expect(formatRunDuration(run('2026-10-24T00:00:00Z', '2026-10-24T00:00:42Z'))).toBe('42 秒')
    expect(formatRunDuration(run('2026-10-24T00:00:00Z', '2026-10-24T00:03:05Z'))).toBe('3 分 5 秒')
    expect(formatRunDuration(run('2026-10-24T00:00:00Z', '2026-10-24T02:30:00Z'))).toBe(
      '2 小时 30 分',
    )
    expect(formatRunDuration(run('2026-10-24T00:00:00Z', null))).toBe('')
  })

  it('运行状态文案', () => {
    expect(runStatusLabel('running')).toBe('执行中')
    expect(runStatusLabel('retry')).toBe('重试中')
    expect(runStatusLabel('weird')).toBe('weird')
  })
})
