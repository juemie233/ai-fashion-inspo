/**
 * F2ResultsPanel 规格测试：本批结果一律用公共组件浏览图片。
 *
 * 锁住三件容易改坏的事：
 * ① 网格容器是 common 的 DensityImageGrid、缩略图是 common 的 ThumbCard
 *    （曾自绘固定列网格，把面板撑出横向滚动条）；
 * ② 列数规格 紧凑 6 / 标准 4 / 宽松 3，且默认「宽松」（图片看大一点）；
 * ③ 已彻底删除的条目用灰色占位卡，点它不会进入勾选。
 */

import { defineComponent, h } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import F2ResultsPanel from '../F2ResultsPanel.vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

// Arco 未在测试环境全局注册：把按钮桩成原生 <button>，便于找到并点击密度切换
const buttonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', this.$slots.default?.())
  },
})
const buttonGroupStub = defineComponent({
  name: 'AButtonGroup',
  render() {
    return h('div', { class: 'btn-group' }, this.$slots.default?.())
  },
})

type ResultItem = Record<string, unknown>

const ITEM: ResultItem = {
  id: 'i1',
  state: 'pending',
  quality_status: 'pending',
  media_type: 'image',
  caption: '下一站再见吧#地铁jk #jk',
  author: '不养羊',
  hashtags: ['jk'],
  file_path: 'images/2026-09/a.webp',
  thumbnail_path: 'thumbnails/2026-09/a.jpg',
  is_favorite: false,
  trash_reason: null,
  source_platform_id: 'f2:abc#image1',
  created_at: '2026-09-15 01:41:19',
}

/** 造一份结果响应（默认单条在库素材） */
function payload(items: ResultItem[] = [ITEM], counts: Partial<Record<string, number>> = {}) {
  return {
    data: {
      task: {
        id: 338,
        status: 'success',
        created_at: '2026-09-15 01:41:19',
        updated_at: '2026-09-15 01:47:33',
        error: null,
        imported: items.length,
        failed: 0,
        fetch_ok: 2,
        fetch_total: 2,
      },
      batch_id: 'f2-20260915-014733-cacf',
      items,
      total: items.length,
      page: 1,
      size: 60,
      counts: {
        total: items.length,
        live: items.length,
        trash: 0,
        gone: 0,
        pending: items.length,
        approved: 0,
        rejected: 0,
        ...counts,
      },
      authors: [{ name: '不养羊', count: items.length }],
      has_batch: true,
    },
  }
}

async function mountPanel(response = payload()) {
  mocks.get.mockResolvedValue(response)
  const wrapper = mount(F2ResultsPanel, {
    props: { taskId: 338 },
    global: { stubs: { 'a-button': buttonStub, 'a-button-group': buttonGroupStub } },
  })
  await flushPromises()
  return wrapper
}

describe('F2ResultsPanel', () => {
  it('图片浏览走 common 的 DensityImageGrid + ThumbCard，默认「宽松」三列', async () => {
    const wrapper = await mountPanel()

    const body = wrapper.find('.density-grid-body')
    expect(body.exists()).toBe(true)
    expect(body.classes()).toContain('density-comfortable')
    expect(body.attributes('style')).toContain('grid-template-columns: repeat(3, minmax(0, 1fr))')
    // 缩略图卡来自 common（内部含 HoverImagePreview 悬停大图）
    expect(wrapper.find('.thumb-card').exists()).toBe(true)
  })

  it('切换密度按钮后按对应列数渲染（紧凑 6 / 标准 4）', async () => {
    const wrapper = await mountPanel()
    // 密度按钮在 DensityImageGrid 的按钮组里（桩组件渲染成 .btn-group）
    const buttons = wrapper.findAll('.density-grid-header .btn-group button')
    expect(buttons.map((b) => b.text())).toEqual(['紧凑', '标准', '宽松'])

    await buttons[0].trigger('click')
    expect(wrapper.find('.density-grid-body').attributes('style')).toContain(
      'repeat(6, minmax(0, 1fr))',
    )

    await buttons[1].trigger('click')
    expect(wrapper.find('.density-grid-body').attributes('style')).toContain(
      'repeat(4, minmax(0, 1fr))',
    )
  })

  it('已彻底删除的条目用灰色占位卡（不渲染缩略图、点它不进勾选）', async () => {
    const wrapper = await mountPanel(
      payload(
        [ITEM, { ...ITEM, id: 'gone-1', state: 'gone', file_path: null, thumbnail_path: null }],
        { gone: 1, live: 1 },
      ),
    )

    expect(wrapper.findAll('.thumb-card')).toHaveLength(1)
    const gone = wrapper.find('.f2r-gone')
    expect(gone.exists()).toBe(true)
    expect(gone.text()).toContain('已彻底删除')

    await gone.trigger('click')
    expect(wrapper.find('.thumb-selected-mask').exists()).toBe(false)
  })
})
