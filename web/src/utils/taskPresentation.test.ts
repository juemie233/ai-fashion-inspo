/**
 * taskPresentation 纯函数单测：任务结果文案汇总与两类任务归一化。
 *
 * 这些逻辑此前内联在 useTaskCenter 中，只能通过 mock axios + 实例化 composable
 * 间接覆盖；抽出为纯函数后，直接给原始对象断言即可，无需任何 mock。
 */

import { describe, expect, it } from 'vitest'
import {
  describeRunningTask,
  formatKeywords,
  isCancelableTaskType,
  isPausableTaskType,
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

  it('f2_import 入库量与计划量一致时只显示入库数', () => {
    const text = summarizeResult(
      'f2_import',
      { plan: { files: 12, skipped: { '已在库（内容相同）': 300 } }, import: { imported: 12 } },
      null,
    )
    expect(text).toBe('入库 12')
  })

  it('f2_import 有失败时补出计划量（完成态不再说「待入库」）', () => {
    const text = summarizeResult(
      'f2_import',
      { plan: { files: 12 }, import: { imported: 10, failed: 2 } },
      null,
    )
    expect(text).toBe('入库 10 · 计划 12')
  })

  it('f2_import 垃圾桶计数优先读结构化字段 trash_skipped', () => {
    const text = summarizeResult(
      'f2_import',
      {
        plan: { files: 0, trash_skipped: 3, skipped: {} },
        import: { imported: 0 },
      },
      null,
    )
    expect(text).toContain('已在垃圾桶 3')
  })

  it('f2_import 旧任务结果回退解析中文跳过原因', () => {
    const legacy = summarizeResult(
      'f2_import',
      {
        plan: { files: 0, skipped: { '已在垃圾桶（不重新导入）': 2 } },
        import: { imported: 0 },
      },
      null,
    )
    expect(legacy).toContain('已在垃圾桶 2')
  })

  it('f2_import 垃圾桶计数为 0 时不展示', () => {
    const none = summarizeResult(
      'f2_import',
      { plan: { files: 5, trash_skipped: 0, skipped: {} }, import: { imported: 5 } },
      null,
    )
    expect(none).not.toContain('垃圾桶')
  })

  it('f2_import 点赞入口（fetch_mode=like）标出「我的喜欢」', () => {
    const text = summarizeResult(
      'f2_import',
      { fetch_mode: 'like', plan: { files: 7 }, import: { imported: 7 } },
      null,
    )
    expect(text).toBe('我的喜欢 · 入库 7')
  })

  it('f2_import 报告自动登记的来源作者博主数（为 0 时不占版面）', () => {
    const withBloggers = summarizeResult(
      'f2_import',
      {
        fetch_mode: 'like',
        plan: { files: 7 },
        import: { imported: 7 },
        bloggers: { created: 12, linked: 7 },
      },
      null,
    )
    expect(withBloggers).toBe('我的喜欢 · 入库 7 · 新登记博主 12')

    const none = summarizeResult(
      'f2_import',
      {
        fetch_mode: 'like',
        plan: { files: 7 },
        import: { imported: 7 },
        bloggers: { created: 0, linked: 0, reused: 3 },
      },
      null,
    )
    expect(none).not.toContain('博主')
  })
})

describe('describeRunningTask', () => {
  it('f2 下载阶段：说明是第几个作者并给出单作者耗时预期', () => {
    const text = describeRunningTask('f2_import', { stage: 'download' }, 'running', 3, 21)
    expect(text).toContain('第 3/21 个作者')
    expect(text).toContain('翻页')
  })

  it('f2 入库阶段：按文件计数，不再说作者', () => {
    const text = describeRunningTask('f2_import', { stage: 'import' }, 'running', 512, 11936)
    expect(text).toContain('第 512/11936 个文件')
    expect(text).not.toContain('作者')
  })

  it('排队/暂停有各自说明，暂停时强调产物保留', () => {
    expect(describeRunningTask('f2_import', null, 'pending', 0, 0)).toContain('排队')
    expect(describeRunningTask('f2_import', { stage: 'download' }, 'paused', 1, 21)).toContain(
      '保留',
    )
  })

  it('非 f2 任务或缺少阶段标记时不编造文案', () => {
    expect(describeRunningTask('batch_analyze', { stage: 'download' }, 'running', 1, 2)).toBe('')
    expect(describeRunningTask('f2_import', {}, 'running', 1, 2)).toBe('')
  })

  it('点赞入口（fetch_mode=like）的下载阶段说明全量翻页', () => {
    const text = describeRunningTask(
      'f2_import',
      { stage: 'download', fetch_mode: 'like' },
      'running',
      0,
      1,
    )
    expect(text).toContain('我的喜欢')
    expect(text).toContain('全量翻页')
  })

  it('点赞下载期把实时文件数写进文案（点赞总数未知，靠它判断在下载）', () => {
    const text = describeRunningTask(
      'f2_import',
      {
        stage: 'download',
        fetch_mode: 'like',
        like_progress: { files: 3517, bytes: 2048, added: 3163 },
      },
      'running',
      0,
      0,
    )
    expect(text).toContain('本次新增 3163 个文件')
    expect(text).toContain('目录内共 3517 个')
    expect(text).toContain('我的喜欢')
  })

  it('扫描与去重阶段单独说明（别让界面停在「下载中」）', () => {
    const text = describeRunningTask('f2_import', { stage: 'scan' }, 'running', 1, 1)
    expect(text).toContain('扫描与去重')
    expect(text).not.toContain('下载中')
  })

  it('入库后的博主登记阶段单独说明', () => {
    const text = describeRunningTask('f2_import', { stage: 'blogger' }, 'running', 1, 1)
    expect(text).toContain('登记来源作者博主')
    expect(text).toContain('不进下载白名单')
  })
})

describe('normalizeQueueTask', () => {
  it('运行中的 f2 任务在「任务」列显示阶段说明而不是空白', () => {
    const t = normalizeQueueTask(
      makeQueueTask({
        type: 'f2_import',
        status: 'running',
        progress: 7,
        done: 4,
        total: 21,
        result: { stage: 'download', fetch: true },
      }),
    )
    expect(t.status).toBe('running')
    expect(t.detail).toContain('第 4/21 个作者')
  })

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

describe('isPausableTaskType（批量/组合分析、标签网络分析、f2 一键获取可暂停）', () => {
  it('四类任务返回 true', () => {
    expect(isPausableTaskType('tag_network_analyze')).toBe(true)
    expect(isPausableTaskType('batch_analyze')).toBe(true)
    expect(isPausableTaskType('multi_analyze')).toBe(true)
    expect(isPausableTaskType('f2_import')).toBe(true)
  })

  it('其他任务类型返回 false', () => {
    expect(isPausableTaskType('quality_check')).toBe(false)
    expect(isPausableTaskType('face_scan')).toBe(false)
    expect(isPausableTaskType('deduplicate')).toBe(false)
    expect(isPausableTaskType('')).toBe(false)
  })
})

describe('isCancelableTaskType（运行中可取消：与后端白名单对齐）', () => {
  it('f2 一键获取素材运行中可取消（执行器有停止逻辑，入口不能缺）', () => {
    expect(isCancelableTaskType('f2_import')).toBe(true)
  })

  it('人脸扫描/匹配与标签网络分析同样可取消', () => {
    expect(isCancelableTaskType('face_scan')).toBe(true)
    expect(isCancelableTaskType('face_match')).toBe(true)
    expect(isCancelableTaskType('tag_network_analyze')).toBe(true)
  })

  it('批量分析等不支持运行中取消（只能暂停）', () => {
    expect(isCancelableTaskType('batch_analyze')).toBe(false)
    expect(isCancelableTaskType('quality_check')).toBe(false)
    expect(isCancelableTaskType('')).toBe(false)
  })
})
