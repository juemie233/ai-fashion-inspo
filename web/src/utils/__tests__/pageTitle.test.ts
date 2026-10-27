/** 页面标题解析的单元测试（标题口径：<页面>[-<子页面>] - AI 穿搭素材库）。 */

import { describe, expect, it } from 'vitest'
import {
  APP_NAME,
  SUBPAGE_SPECS,
  formatDocumentTitle,
  resolveDocumentTitle,
  resolveRouteSegment,
} from '../pageTitle'

const meta = (title?: string) => ({ title })

describe('formatDocumentTitle', () => {
  it('拼成「页面 - 站点名」', () => {
    expect(formatDocumentTitle('素材库')).toBe(`素材库 - ${APP_NAME}`)
  })

  it('空段落回退站点名（不出现「 - AI 穿搭素材库」）', () => {
    expect(formatDocumentTitle('')).toBe(APP_NAME)
    expect(formatDocumentTitle(null)).toBe(APP_NAME)
    expect(formatDocumentTitle(undefined)).toBe(APP_NAME)
    expect(formatDocumentTitle('   ')).toBe(APP_NAME)
  })

  it('段落本身就是站点名时不重复拼接', () => {
    expect(formatDocumentTitle(APP_NAME)).toBe(APP_NAME)
  })
})

describe('resolveRouteSegment', () => {
  it('未登记子页面的页面只显示页面名', () => {
    expect(resolveRouteSegment({ name: 'home', meta: meta('素材库'), query: {} })).toBe('素材库')
  })

  it('URL 驱动的子页面按 query 解析（素材管理-手机图剪裁）', () => {
    expect(
      resolveRouteSegment({ name: 'admin', meta: meta('素材管理'), query: { tab: 'crop' } }),
    ).toBe('素材管理-手机图剪裁')
  })

  it('缺省子页面用 defaultKey（素材管理-概览）', () => {
    expect(resolveRouteSegment({ name: 'admin', meta: meta('素材管理'), query: {} })).toBe(
      '素材管理-概览',
    )
  })

  it('query 为数组时取第一个（vue-router 重复参数）', () => {
    expect(
      resolveRouteSegment({
        name: 'admin',
        meta: meta('素材管理'),
        query: { tab: ['crop', 'ai'] },
      }),
    ).toBe('素材管理-手机图剪裁')
  })

  it('未知子页面 key 回退成页面名（不显示英文字面量）', () => {
    expect(
      resolveRouteSegment({ name: 'admin', meta: meta('素材管理'), query: { tab: 'nope' } }),
    ).toBe('素材管理')
  })

  it('人物管理按 ?kind= 区分穿搭博主/职业模特', () => {
    expect(resolveRouteSegment({ name: 'persons', meta: meta('人物管理'), query: {} })).toBe(
      '人物管理-穿搭博主',
    )
    expect(
      resolveRouteSegment({ name: 'persons', meta: meta('人物管理'), query: { kind: 'model' } }),
    ).toBe('人物管理-职业模特')
  })

  it('meta.title 缺失或不是字符串时回退站点名', () => {
    expect(resolveRouteSegment({ name: 'home', meta: null, query: {} })).toBe(APP_NAME)
    expect(resolveRouteSegment({ name: 'home', meta: { title: 42 }, query: {} })).toBe(APP_NAME)
    expect(resolveRouteSegment({ name: 'home', meta: meta('  '), query: {} })).toBe(APP_NAME)
  })
})

describe('resolveDocumentTitle', () => {
  it('拼上站点名', () => {
    expect(
      resolveDocumentTitle({ name: 'models', meta: meta('AI 模型管理'), query: { tab: 'queue' } }),
    ).toBe(`AI 模型管理-标签分析 - ${APP_NAME}`)
  })

  it('未登记 name（含 undefined）不影响页面名', () => {
    expect(resolveDocumentTitle({ name: undefined, meta: meta('高级搜索'), query: {} })).toBe(
      `高级搜索 - ${APP_NAME}`,
    )
  })

  it('登记表里的每个子页面 key 都能解析成中文标签（防写错 key / 漏标签）', () => {
    for (const [name, spec] of Object.entries(SUBPAGE_SPECS)) {
      expect(spec.labels[spec.defaultKey ?? '']).toBeTruthy()
      for (const [key, label] of Object.entries(spec.labels)) {
        const title = resolveDocumentTitle({
          name,
          meta: meta('页面'),
          query: { [spec.queryKey]: key },
        })
        expect(title).toBe(`页面-${label} - ${APP_NAME}`)
      }
    }
  })
})
