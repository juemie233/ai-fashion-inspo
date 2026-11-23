/**
 * 通用格式化工具**边界补测**：既有 `format.test.ts` 覆盖了主要函数，
 * 本文件补低覆盖分支与临界值：
 *
 * - formatVram / formatUptime / safeJsonParse：此前没有用例的函数；
 * - formatBytes / formatSize 的临界值（1023/1024、TB 上限钳制）；
 * - formatCount 的万/亿临界值、负数与 NaN；
 * - formatAge 的分钟/小时/天/月/年分级与未来时间；
 * - formatDate / formatDateTime / formatMs 的空值与非法值。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  formatAge,
  formatBytes,
  formatCount,
  formatDate,
  formatDateTime,
  formatMs,
  formatSize,
  formatUptime,
  formatVram,
  safeJsonParse,
} from '../format'

beforeEach(() => {
  vi.useFakeTimers()
  // 固定「现在」：2026-11-23 12:00:00 UTC，formatAge 的差值计算可复现
  vi.setSystemTime(new Date('2026-11-23T12:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('formatVram', () => {
  it('0/空值显示 -，其它交给 formatBytes', () => {
    expect(formatVram(0)).toBe('-')
    expect(formatVram(512)).toBe('512.0 B')
    expect(formatVram(1024)).toBe('1.0 KB')
    expect(formatVram(1024 ** 3)).toBe('1.0 GB')
  })
})

describe('formatUptime', () => {
  it('空值/负数回退 -', () => {
    expect(formatUptime(null)).toBe('-')
    expect(formatUptime(undefined)).toBe('-')
    expect(formatUptime(-1)).toBe('-')
  })

  it('秒/分钟/小时/天分级与余数拼接', () => {
    expect(formatUptime(0)).toBe('0 秒')
    expect(formatUptime(59)).toBe('59 秒')
    expect(formatUptime(60)).toBe('1 分钟')
    expect(formatUptime(3599)).toBe('59 分钟')
    expect(formatUptime(3600)).toBe('1 小时')
    expect(formatUptime(3660)).toBe('1 小时 1 分钟')
    expect(formatUptime(86400)).toBe('1 天')
    expect(formatUptime(90000)).toBe('1 天 1 小时')
    expect(formatUptime(172800)).toBe('2 天')
  })
})

describe('safeJsonParse', () => {
  it('空输入直接返回 fallback，不调用 JSON.parse', () => {
    const fallback = { a: 1 }
    expect(safeJsonParse(null, fallback)).toBe(fallback)
    expect(safeJsonParse(undefined, fallback)).toBe(fallback)
    expect(safeJsonParse('', fallback)).toBe(fallback)
  })

  it('合法 JSON 正常解析（对象/数组/字面量）', () => {
    expect(safeJsonParse('{"a":1}', null)).toEqual({ a: 1 })
    expect(safeJsonParse('[1,2]', [])).toEqual([1, 2])
    expect(safeJsonParse('"text"', '')).toBe('text')
    // 'null' 是合法 JSON：返回 null 而不是 fallback
    expect(safeJsonParse('null', { fallback: true })).toBeNull()
    expect(safeJsonParse('0', 9)).toBe(0)
  })

  it('非法 JSON 回退 fallback（localStorage 脏数据容错）', () => {
    const fallback = { theme: 'light' }
    expect(safeJsonParse('{oops', fallback)).toBe(fallback)
    expect(safeJsonParse('undefined', fallback)).toBe(fallback)
  })
})

describe('formatBytes 临界值', () => {
  it('0 与 <1KB 按字节显示；1024 进位；超过 TB 时钳制在 TB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023.0 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 ** 4)).toBe('1.0 TB')
    // 单位表到 TB 为止，继续增长只放大数值不换单位
    expect(formatBytes(1024 ** 5)).toBe('1024.0 TB')
  })
})

describe('formatSize 临界值', () => {
  it('1024 的整数倍边界切换单位', () => {
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(1023)).toBe('1023 B')
    expect(formatSize(1024)).toBe('1 KB')
    expect(formatSize(1024 * 1024 - 1)).toBe('1024 KB')
    expect(formatSize(1024 * 1024)).toBe('1.0 MB')
    expect(formatSize(1024 ** 3)).toBe('1.00 GB')
  })
})

describe('formatCount 万/亿临界值', () => {
  it('空值/NaN 显示 -；万/亿压缩且去掉多余的 .0', () => {
    expect(formatCount(null)).toBe('-')
    expect(formatCount(undefined)).toBe('-')
    expect(formatCount(Number.NaN)).toBe('-')
    expect(formatCount(0)).toBe('0')
    expect(formatCount(9999)).toBe('9999')
    expect(formatCount(10000)).toBe('1万')
    expect(formatCount(12345)).toBe('1.2万')
    expect(formatCount(100000)).toBe('10万')
    expect(formatCount(99_999_999)).toBe('10000万')
    expect(formatCount(100_000_000)).toBe('1亿')
    expect(formatCount(123_456_789)).toBe('1.2亿')
    // 负数按绝对值判断量级，符号保留
    expect(formatCount(-12345)).toBe('-1.2万')
  })
})

describe('formatAge 时效分级', () => {
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

  it('未来时间/2 分钟内显示「刚刚」', () => {
    expect(formatAge(new Date(Date.now() + 60_000).toISOString())).toBe('刚刚')
    expect(formatAge(ago(30_000))).toBe('刚刚')
    expect(formatAge(ago(119_000))).toBe('刚刚')
  })

  it('分钟/小时/天/月/年逐级切换', () => {
    expect(formatAge(ago(2 * 60_000))).toBe('2 分钟前')
    expect(formatAge(ago(59 * 60_000))).toBe('59 分钟前')
    expect(formatAge(ago(90 * 60_000))).toBe('1 小时前')
    expect(formatAge(ago(25 * 3600_000))).toBe('1 天前')
    expect(formatAge(ago(35 * 24 * 3600_000))).toBe('1 个月前')
    expect(formatAge(ago(400 * 24 * 3600_000))).toBe('1 年前')
  })

  it('空值/非法值返回空串', () => {
    expect(formatAge(null)).toBe('')
    expect(formatAge(undefined)).toBe('')
    expect(formatAge('not-a-date')).toBe('')
  })
})

describe('时间相关边界', () => {
  it('formatDate：空值、非法值统一 -；合法值输出本地化时间', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate(undefined)).toBe('-')
    expect(formatDate('')).toBe('-')
    expect(formatDate('not-a-date')).toBe('-')
    expect(formatDate('2026-11-23T12:00:00Z')).not.toBe('-')
  })

  it('formatDateTime：接受 Date 对象；非法值回退 -；withSeconds 追加秒', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime('bad')).toBe('-')
    expect(formatDateTime(new Date(2026, 0, 5, 9, 3, 7))).toBe('2026-01-05 09:03')
    expect(formatDateTime(new Date(2026, 0, 5, 9, 3, 7), true)).toBe('2026-01-05 09:03:07')
  })

  it('formatMs：null 回退 -；<1000 用 ms；>=1000 换算 s', () => {
    expect(formatMs(null)).toBe('-')
    expect(formatMs(999)).toBe('999ms')
    expect(formatMs(1000)).toBe('1.0s')
    expect(formatMs(1500)).toBe('1.5s')
  })
})
