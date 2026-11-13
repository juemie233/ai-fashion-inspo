/** 状态标签：任务默认语义 + 质量/AI/分析/垃圾桶域内映射 + badge 变体。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import StatusTag from '../StatusTag.vue'

function mountTag(status: string, props: Record<string, unknown> = {}) {
  return mount(StatusTag, {
    props: { status, ...props },
    global: { plugins: [ArcoVue] },
  })
}

describe('StatusTag', () => {
  it.each([
    ['pending', '排队中'],
    ['running', '运行中'],
    ['success', '已完成'],
    ['completed', '已完成'],
    ['failed', '失败'],
    ['cancelled', '已取消'],
  ])('任务 %s → %s', (status, label) => {
    expect(mountTag(status).text()).toBe(label)
  })

  it('未知任务状态原样展示，不吞成空标签', () => {
    expect(mountTag('weird-status').text()).toBe('weird-status')
  })

  it('质量状态：拒绝态带默认原因 title，未知值回退原文', () => {
    const rejected = mountTag('rejected', { kind: 'quality' })
    expect(rejected.text()).toBe('已拒绝')
    expect(rejected.attributes('title')).toContain('非穿搭内容')

    expect(mountTag('pending', { kind: 'quality' }).text()).toBe('待审核')
    expect(mountTag('weird', { kind: 'quality' }).text()).toBe('weird')
  })

  it('AI 标记：本地出图与疑似 AI 文案不同，无标记时不渲染', () => {
    const generated = mountTag('ai', { kind: 'ai', sourceType: 'generated' })
    expect(generated.text()).toBe('AI 生成')
    expect(generated.attributes('title')).toContain('不是真实拍摄')

    const suspected = mountTag('ai', { kind: 'ai', sourceType: 'douyin' })
    expect(suspected.text()).toBe('疑似 AI')

    expect(mountTag('none', { kind: 'ai' }).text()).toBe('')
  })

  it('分析状态与垃圾桶原因走统一映射', () => {
    expect(mountTag('done', { kind: 'analysis' }).text()).toBe('已分析')
    expect(mountTag('error', { kind: 'analysis' }).text()).toBe('分析失败')
    expect(mountTag('重复', { kind: 'trash' }).text()).toBe('重复')
    expect(mountTag('AI生成', { kind: 'trash' }).text()).toBe('AI生成')
  })

  it('badge 变体渲染自绘小徽标（素材卡左上角）', () => {
    const badge = mountTag('pending', { kind: 'quality', variant: 'badge' })
    const span = badge.find('.status-badge')
    expect(span.exists()).toBe(true)
    expect(span.text()).toBe('待审核')
    expect(span.attributes('style')).toContain('background')
    expect(badge.find('.arco-tag').exists()).toBe(false)
  })
})
