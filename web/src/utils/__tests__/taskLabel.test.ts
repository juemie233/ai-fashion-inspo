/** 任务标签工具单测。 */

import { describe, expect, it } from 'vitest'
import {
  SCRAPER_PLATFORM_LABELS,
  TASK_TYPE_ICONS,
  TASK_TYPE_LABELS,
  formatDuration,
  normalizeTaskStatus,
  taskStatusType,
  taskTypeTagColor,
} from '../taskLabel'

describe('TASK_TYPE_LABELS', () => {
  it('全量任务类型映射为中文，vector_backfill 不得被错误归类', () => {
    expect(TASK_TYPE_LABELS.batch_analyze).toBe('批量 AI 分析')
    expect(TASK_TYPE_LABELS.quality_check).toBe('质量审核')
    expect(TASK_TYPE_LABELS.batch_delete).toBe('批量删除')
    expect(TASK_TYPE_LABELS.deduplicate).toBe('近似重复检测删除')
    expect(TASK_TYPE_LABELS.scraper).toBe('采集')
    expect(TASK_TYPE_LABELS.vector_backfill).toBe('向量回填')
  })
})

describe('TASK_TYPE_ICONS', () => {
  it('全量任务类型均有图标映射，删除类任务图标互不相同', () => {
    for (const type of Object.keys(TASK_TYPE_LABELS)) {
      expect(TASK_TYPE_ICONS[type], `类型 ${type} 缺少图标映射`).toBeTruthy()
    }
    // 两类删除任务必须使用不同图标，保证列表中一眼可辨
    expect(TASK_TYPE_ICONS.batch_delete).not.toBe(TASK_TYPE_ICONS.deduplicate)
  })
})

describe('normalizeTaskStatus', () => {
  it('completed 归一化为 success', () => {
    expect(normalizeTaskStatus('completed')).toBe('success')
    expect(normalizeTaskStatus('running')).toBe('running')
  })
})

describe('taskStatusType', () => {
  it('状态到颜色映射', () => {
    expect(taskStatusType('success')).toBe('success')
    expect(taskStatusType('failed')).toBe('error')
    expect(taskStatusType('running')).toBe('info')
    expect(taskStatusType('pending')).toBe('default')
    expect(taskStatusType('unknown')).toBe('default')
  })
})

describe('taskTypeTagColor', () => {
  it('类型到颜色映射，未知回退 default', () => {
    expect(taskTypeTagColor('batch_delete')).toBe('error')
    expect(taskTypeTagColor('batch_analyze')).toBe('primary')
    expect(taskTypeTagColor('unknown_type')).toBe('default')
  })
})

describe('SCRAPER_PLATFORM_LABELS', () => {
  it('各采集平台映射为中文（含浏览器插件）', () => {
    expect(SCRAPER_PLATFORM_LABELS.xiaohongshu).toBe('小红书')
    expect(SCRAPER_PLATFORM_LABELS.douyin).toBe('抖音')
    expect(SCRAPER_PLATFORM_LABELS.browser_extension).toBe('浏览器插件')
  })
})

describe('formatDuration', () => {
  it('各时长范围', () => {
    expect(formatDuration(5)).toBe('5 秒')
    expect(formatDuration(90)).toBe('2 分钟')
    expect(formatDuration(3600)).toBe('1 小时')
    expect(formatDuration(5400)).toBe('1 小时 30 分')
  })

  it('非法输入返回空串', () => {
    expect(formatDuration(0)).toBe('')
    expect(formatDuration(NaN)).toBe('')
    expect(formatDuration(-5)).toBe('')
  })
})
