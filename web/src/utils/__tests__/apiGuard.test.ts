/**
 * API 响应形状校验：warnShape / warnItems —— 只告警不阻断，且必须原样返回数据。
 *
 * 这套工具是「后端字段漂移在运行时可见」的兜底（此前缺 matched_blogger_name、
 * 队列统计恒为 0 都无提示）。单测要钉住三点：
 * 1. 类型判断口径：可空 `?` 只放过 null（undefined 仍算缺失）、array 用 Array.isArray、
 *    object 排除 null；
 * 2. 告警文案包含标签 + 字段名 + 期望类型 + 实际类型，便于 grep 定位；
 * 3. 返回值必须是原数据（同一引用），校验逻辑不能改变业务数据。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ error: vi.fn() }))

vi.mock('@/utils/logger', () => ({ logger: { error: mocks.error } }))

import { warnItems, warnShape } from '../apiGuard'

/** 取出所有错误日志文案，便于断言 */
function errorMessages(): string[] {
  return mocks.error.mock.calls.map((call) => String(call[0]))
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('warnShape', () => {
  it('非对象响应：记录实际类型并原样返回', () => {
    expect(warnShape(null, { id: 'number' }, '列表')).toBeNull()
    expect(warnShape('oops', { id: 'number' }, '列表')).toBe('oops')
    expect(warnShape(undefined, { id: 'number' }, '列表')).toBeUndefined()

    expect(errorMessages()).toEqual([
      '[apiGuard] 列表: 响应不是对象（实际 null）',
      '[apiGuard] 列表: 响应不是对象（实际 string）',
      '[apiGuard] 列表: 响应不是对象（实际 缺失）',
    ])
  })

  it('字段全部匹配时不告警，且返回同一对象引用', () => {
    const data = { id: 1, name: '白裙', tags: ['a'], meta: { x: 1 }, ok: true }

    const result = warnShape<typeof data>(
      data,
      { id: 'number', name: 'string', tags: 'array', meta: 'object', ok: 'boolean' },
      '素材',
    )

    expect(result).toBe(data)
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('缺失与类型错都告警，文案带期望/实际类型', () => {
    const data = { id: '7', name: null }

    warnShape(data, { id: 'number', name: 'string', count: 'number' }, '素材')

    expect(errorMessages()).toEqual([
      '[apiGuard] 素材: 字段 id 期望 number，实际 string',
      '[apiGuard] 素材: 字段 name 期望 string，实际 null',
      '[apiGuard] 素材: 字段 count 期望 number，实际 缺失',
    ])
  })

  it('可空类型 ? 只放过 null：undefined 仍按「缺失」告警', () => {
    const data = { rating: null } as { rating: number | null; note?: string | null }

    warnShape(data, { rating: 'number?', note: 'string?' }, '评分')

    expect(errorMessages()).toEqual(['[apiGuard] 评分: 字段 note 期望 string?，实际 缺失'])
  })

  it('array 用 Array.isArray 判定：类数组对象不算数组', () => {
    warnShape({ items: { length: 1 } }, { items: 'array' }, '列表')

    expect(errorMessages()).toEqual(['[apiGuard] 列表: 字段 items 期望 array，实际 object'])
  })

  it('实际值是数组时日志输出 array（便于区分「数组当对象用」的漂移）', () => {
    warnShape({ count: [] }, { count: 'number' }, '统计')

    expect(errorMessages()).toEqual(['[apiGuard] 统计: 字段 count 期望 number，实际 array'])
  })

  it('object 类型排除 null（typeof null === "object" 不能误判通过）', () => {
    warnShape({ extra: null }, { extra: 'object' }, '扩展')

    expect(errorMessages()).toEqual(['[apiGuard] 扩展: 字段 extra 期望 object，实际 null'])
  })
})

describe('warnItems', () => {
  it('非数组响应：记录期望数组并原样返回', () => {
    const payload = { items: [] }

    expect(warnItems(payload, { id: 'number' }, '列表')).toBe(payload)

    expect(errorMessages()).toEqual(['[apiGuard] 列表: 期望数组（实际 object）'])
  })

  it('元素不是对象时跳过该元素并继续校验后续元素', () => {
    const data = [null, { id: 'bad' }, { id: 3 }]

    const result = warnItems(data, { id: 'number' }, '列表')

    expect(result).toBe(data)
    expect(errorMessages()).toEqual([
      '[apiGuard] 列表: 数组元素不是对象',
      '[apiGuard] 列表: 元素字段 id 期望 number，实际 string',
    ])
  })

  it('全部元素匹配时不告警，返回同一数组引用', () => {
    const data = [
      { id: 1, name: 'a' },
      { id: 2, name: 'b' },
    ]

    expect(warnItems(data, { id: 'number', name: 'string' }, '列表')).toBe(data)
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('嵌套可空字段按 ? 口径校验（null 通过、其它类型与 undefined 告警）', () => {
    const data = [{ matched: null }, { matched: '5' }, { matched: undefined }]

    warnItems(data, { matched: 'number?' }, '人脸检测')

    expect(errorMessages()).toEqual([
      '[apiGuard] 人脸检测: 元素字段 matched 期望 number?，实际 string',
      '[apiGuard] 人脸检测: 元素字段 matched 期望 number?，实际 缺失',
    ])
  })
})
