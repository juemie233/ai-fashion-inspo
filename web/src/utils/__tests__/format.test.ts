/** 格式化工具函数单测。 */

import { describe, expect, it } from 'vitest'
import {
  formatBytes,
  formatDate,
  formatMs,
  formatSize,
  fmtSize,
  normalizeModelName,
  smartSize,
} from '../format'

describe('formatBytes', () => {
  it('零与非法输入', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(NaN)).toBe('0 B')
  })

  it('各数量级', () => {
    expect(formatBytes(512)).toBe('512.0 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('formatSize', () => {
  it('字节与各数量级', () => {
    expect(formatSize(100)).toBe('100 B')
    expect(formatSize(2048)).toBe('2 KB')
    expect(formatSize(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatSize(2 * 1024 ** 3)).toBe('2.00 GB')
  })
})

describe('smartSize / fmtSize', () => {
  it('返回数值与单位', () => {
    expect(smartSize(512)).toEqual({ value: '512', unit: 'B' })
    expect(smartSize(2048)).toEqual({ value: '2.0', unit: 'KB' })
    expect(fmtSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('formatMs', () => {
  it('null 与各量级', () => {
    expect(formatMs(null)).toBe('-')
    expect(formatMs(500)).toBe('500ms')
    expect(formatMs(2500)).toBe('2.5s')
  })
})

describe('formatDate', () => {
  it('非法输入回退', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate('not-a-date')).toBe('-')
  })

  it('合法日期', () => {
    expect(formatDate('2026-08-16T10:00:00Z')).toContain('2026')
  })
})

describe('normalizeModelName', () => {
  it('无 tag 补 :latest', () => {
    expect(normalizeModelName('all-minilm')).toBe('all-minilm:latest')
  })

  it('已有 tag 保持不变', () => {
    expect(normalizeModelName('all-minilm:latest')).toBe('all-minilm:latest')
    expect(normalizeModelName('qwen3-vl:8b-instruct')).toBe('qwen3-vl:8b-instruct')
  })

  it('空字符串原样返回', () => {
    expect(normalizeModelName('')).toBe('')
  })
})
