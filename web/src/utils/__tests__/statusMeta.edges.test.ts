/**
 * 状态标签元数据（utils/statusMeta）**补齐用例**：该文件此前没有独立单测（覆盖率来自
 * 视图间接调用），本文件把四组映射的所有分支锁住，避免以后改颜色/文案时页面漂移：
 *
 * - aiStatusMeta：非 AI 返回 null；generated 与「疑似 AI」两种文案/标题；
 * - analysisStatusMeta：none/analyzing/error/done/success 与未知值兜底；
 * - trashReasonMeta：六种垃圾桶原因 + 未知/null；
 * - qualityStatusMeta：三种审核状态 + 未知/null；
 * - bg 色统一取自 STATUS_BG（不各写一份）。
 */

import { describe, expect, it } from 'vitest'

import { STATUS_BG } from '@/utils/colors'
import { aiStatusMeta, analysisStatusMeta, qualityStatusMeta, trashReasonMeta } from '../statusMeta'

describe('aiStatusMeta', () => {
  it('非 AI 返回 null（即使带了来源类型）', () => {
    expect(aiStatusMeta(false)).toBeNull()
    expect(aiStatusMeta(false, 'generated')).toBeNull()
  })

  it('本地出图生成的 AI 素材：AI 生成 + 本地出图说明', () => {
    expect(aiStatusMeta(true, 'generated')).toEqual({
      label: 'AI 生成',
      color: 'purple',
      bg: STATUS_BG.ai,
      title: '由本地出图生成（不是真实拍摄）',
    })
  })

  it('疑似 AI（非本地出图）：文案与提示为人工确认', () => {
    expect(aiStatusMeta(true)).toEqual({
      label: '疑似 AI',
      color: 'purple',
      bg: STATUS_BG.ai,
      title: '疑似 AI 生成，请人工确认',
    })
    expect(aiStatusMeta(true, null)?.label).toBe('疑似 AI')
    expect(aiStatusMeta(true, 'douyin')?.label).toBe('疑似 AI')
  })
})

describe('analysisStatusMeta', () => {
  it('五种已知分析状态', () => {
    expect(analysisStatusMeta('none')).toEqual({
      label: '尚未分析',
      color: 'gray',
      bg: STATUS_BG.neutral,
    })
    expect(analysisStatusMeta('analyzing')).toEqual({
      label: '分析中...',
      color: 'arcoblue',
      bg: STATUS_BG.primary,
    })
    expect(analysisStatusMeta('error')).toEqual({
      label: '分析失败',
      color: 'red',
      bg: STATUS_BG.danger,
    })
    // 后端历史字段 done 与 success 同义
    expect(analysisStatusMeta('done').label).toBe('已分析')
    expect(analysisStatusMeta('success').label).toBe('已分析')
    expect(analysisStatusMeta('done').bg).toBe(STATUS_BG.success)
  })

  it('未知状态原样展示为灰色；空值显示「未知」', () => {
    expect(analysisStatusMeta('queued_v2')).toEqual({
      label: 'queued_v2',
      color: 'gray',
      bg: STATUS_BG.neutral,
    })
    expect(analysisStatusMeta(null).label).toBe('未知')
    expect(analysisStatusMeta(undefined).label).toBe('未知')
    expect(analysisStatusMeta('').label).toBe('未知')
  })
})

describe('trashReasonMeta', () => {
  it('六种垃圾桶原因都有中文文案与语义色', () => {
    expect(trashReasonMeta('质量差')).toEqual({
      label: '质量差',
      color: 'red',
      bg: STATUS_BG.danger,
    })
    expect(trashReasonMeta('重复')).toEqual({
      label: '重复',
      color: 'orange',
      bg: STATUS_BG.warning,
    })
    expect(trashReasonMeta('不喜欢').color).toBe('gray')
    expect(trashReasonMeta('隐私').color).toBe('red')
    expect(trashReasonMeta('AI生成').color).toBe('purple')
    expect(trashReasonMeta('其他').color).toBe('gray')
  })

  it('未知原因原样展示；空值显示「未知」', () => {
    expect(trashReasonMeta('手动清理')).toEqual({
      label: '手动清理',
      color: 'gray',
      bg: STATUS_BG.neutral,
    })
    expect(trashReasonMeta(null).label).toBe('未知')
    expect(trashReasonMeta(undefined).label).toBe('未知')
    expect(trashReasonMeta('').label).toBe('未知')
  })
})

describe('qualityStatusMeta', () => {
  it('三种审核状态；被拒带默认 title', () => {
    expect(qualityStatusMeta('approved')).toEqual({
      label: '已通过',
      color: 'green',
      bg: STATUS_BG.success,
    })
    expect(qualityStatusMeta('pending')).toEqual({
      label: '待审核',
      color: 'orange',
      bg: STATUS_BG.neutral,
    })
    expect(qualityStatusMeta('rejected')).toEqual({
      label: '已拒绝',
      color: 'red',
      bg: STATUS_BG.danger,
      title: 'AI 判定为非穿搭内容',
    })
  })

  it('未知状态原样展示；空值显示「未知」', () => {
    expect(qualityStatusMeta('need_review')).toEqual({
      label: 'need_review',
      color: 'gray',
      bg: STATUS_BG.neutral,
    })
    expect(qualityStatusMeta(null).label).toBe('未知')
    expect(qualityStatusMeta(undefined).label).toBe('未知')
  })
})
