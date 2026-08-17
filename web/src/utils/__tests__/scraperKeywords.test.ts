/** scraperKeywords 采集任务历史关键词提取单测。 */

import { describe, expect, it } from 'vitest'
import { extractHistoryKeywords, parseKeywords } from '../scraperKeywords'

describe('parseKeywords', () => {
  it('解析 config 中的关键词列表', () => {
    expect(parseKeywords(JSON.stringify({ keywords: ['连衣裙', '半身裙'], max_count: 50 }))).toEqual(['连衣裙', '半身裙'])
  })

  it('config 为空/非法/无 keywords 时返回空数组', () => {
    expect(parseKeywords(null)).toEqual([])
    expect(parseKeywords('')).toEqual([])
    expect(parseKeywords('not-json')).toEqual([])
    expect(parseKeywords(JSON.stringify({ max_count: 10 }))).toEqual([])
    expect(parseKeywords(JSON.stringify({ keywords: '字符串' }))).toEqual([])
  })

  it('过滤非字符串项', () => {
    expect(parseKeywords(JSON.stringify({ keywords: ['有效', 42, null] }))).toEqual(['有效'])
  })
})

describe('extractHistoryKeywords', () => {
  it('只提取已完成任务的关键词', () => {
    const tasks = [
      { status: 'completed', config: JSON.stringify({ keywords: ['连衣裙', '半身裙'], max_count: 50 }) },
      { status: 'running', config: JSON.stringify({ keywords: ['跑步中关键词'], max_count: 10 }) },
      { status: 'failed', config: JSON.stringify({ keywords: ['失败关键词'], max_count: 10 }) },
      { status: 'cancelled', config: JSON.stringify({ keywords: ['取消关键词'], max_count: 10 }) },
    ]
    expect(extractHistoryKeywords(tasks)).toEqual(['连衣裙', '半身裙'])
  })

  it('按任务顺序去重，最近使用过的关键词在前', () => {
    const tasks = [
      { status: 'completed', config: JSON.stringify({ keywords: ['JK制服'], max_count: 20 }) },
      { status: 'completed', config: JSON.stringify({ keywords: ['连衣裙', 'JK制服'], max_count: 20 }) },
    ]
    expect(extractHistoryKeywords(tasks)).toEqual(['JK制服', '连衣裙'])
  })

  it('跳过脏数据（config 非 JSON / keywords 非数组 / 空词）', () => {
    const tasks = [
      { status: 'completed', config: 'not-json{', keywords: null },
      { status: 'completed', config: JSON.stringify({ keywords: '连衣裙' }) },
      { status: 'completed', config: JSON.stringify({ keywords: ['  ', '有效词', 42] }) },
      { status: 'completed', config: null },
    ]
    expect(extractHistoryKeywords(tasks)).toEqual(['有效词'])
  })

  it('无已完成任务时返回空数组', () => {
    expect(extractHistoryKeywords([])).toEqual([])
    expect(extractHistoryKeywords([{ status: 'pending', config: '{}' }])).toEqual([])
  })
})
