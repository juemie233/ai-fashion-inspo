/**
 * 素材卡的「AI 生成 / 疑似 AI」徽标：同一个 `is_ai_generated` 标记，两种文案。
 *
 * 需求（2026-10-28）：本地出图的图片要标成「AI 生成」，不是加标签，而是用与「疑似 AI」
 * 相同的标记。后端已保证生成图始终 `is_ai_generated=true`（审核不覆盖、翻案跳过），
 * 前端据此用 `source_type` 决定文案：
 * - `source_type === 'generated'` → 「AI 生成」，提示「由本地出图生成（不是真实拍摄）」；
 * - 其它来源 → 「疑似 AI」，提示「疑似 AI 生成，请人工确认」。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import InspirationCard from '../InspirationCard.vue'
import type { InspirationOut } from '@/api/inspirations'

function item(overrides: Partial<InspirationOut> = {}): InspirationOut {
  return {
    id: 'insp-1',
    file_path: 'images/2026-10/a.jpg',
    thumbnail_path: 'thumbnails/2026-10/a.jpg',
    media_type: 'image',
    source_type: 'manual_upload',
    is_favorite: false,
    created_at: '2026-10-26T12:00:00Z',
    tags: [],
    analysis_status: 'done',
    quality_status: 'approved',
    is_ai_generated: false,
    ...overrides,
  } as InspirationOut
}

function mountCard(row: InspirationOut) {
  return mount(InspirationCard, {
    props: { item: row },
    global: { plugins: [ArcoVue] },
  })
}

describe('素材卡 AI 徽标', () => {
  it('本地出图显示「AI 生成」，并说明不是真实拍摄', () => {
    const wrapper = mountCard(item({ source_type: 'generated', is_ai_generated: true }))
    const badge = wrapper.find('.quality-badge.ai')

    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('AI 生成')
    expect(badge.attributes('title')).toContain('不是真实拍摄')
    expect(wrapper.text()).not.toContain('疑似 AI')

    wrapper.unmount()
  })

  it('普通来源的标记仍显示「疑似 AI」', () => {
    const wrapper = mountCard(item({ source_type: 'douyin', is_ai_generated: true }))
    const badge = wrapper.find('.quality-badge.ai')

    expect(badge.text()).toBe('疑似 AI')
    expect(badge.attributes('title')).toContain('请人工确认')

    wrapper.unmount()
  })

  it('没有标记就不出徽标', () => {
    const wrapper = mountCard(item({ is_ai_generated: false }))
    expect(wrapper.find('.quality-badge.ai').exists()).toBe(false)

    wrapper.unmount()
  })
})
