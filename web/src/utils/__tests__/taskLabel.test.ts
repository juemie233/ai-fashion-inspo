/** 任务标签工具单测。 */

import { describe, expect, it } from 'vitest'
import {
  formatDuration,
  normalizeTaskStatus,
  taskStatusType,
  taskTypeTagColor,
} from '../taskLabel'

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
