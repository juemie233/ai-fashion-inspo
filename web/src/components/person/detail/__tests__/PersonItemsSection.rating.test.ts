/** 人物详情「全部」素材网格的评分透传（2026-11-12 博主作品评分方案）。 */

import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import PersonItemsSection from '../PersonItemsSection.vue'

const MasonryStub = defineComponent({
  name: 'MasonryGrid',
  props: {
    items: { type: Array, default: () => [] },
    showRating: { type: Boolean, default: true },
    showFavorite: { type: Boolean, default: false },
    showActions: { type: Boolean, default: false },
  },
  emits: ['rate', 'toggle-favorite'],
  template: '<div class="masonry-stub" />',
})

const ITEM = {
  inspiration_id: 'insp-1',
  file_path: 'images/a.jpg',
  thumbnail_path: 'thumbnails/a.jpg',
  media_type: 'image',
  confidence: 1,
  created_at: '2026-11-12T00:00:00Z',
  quality_status: 'approved' as const,
  rating: 4,
  is_favorite: false,
}

function mountSection(kind: 'blogger' | 'model') {
  return mount(PersonItemsSection, {
    props: {
      kind,
      items: [ITEM],
      total: 1,
      page: 1,
      pageSize: 20,
      loading: false,
      filters: {},
      sort: 'newest',
      viewMode: 'list',
      works: [],
      worksTotal: 0,
      worksTotalItems: 0,
      worksPage: 1,
      worksPageSize: 12,
      worksLoading: false,
      tagOptions: [],
      unbindMode: false,
      unbindSelectedIds: new Set<string>(),
      unbindSubmitting: false,
      clearingAll: false,
      isCurrentPageAllSelected: false,
      hasImageItems: true,
    },
    global: { plugins: [ArcoVue], stubs: { MasonryGrid: MasonryStub } },
  })
}

describe('PersonItemsSection 评分', () => {
  it('博主「全部」视图开启评分并透传 rating', () => {
    const wrapper = mountSection('blogger')
    const grid = wrapper.findComponent({ name: 'MasonryGrid' })
    expect(grid.props('showRating')).toBe(true)
    expect(grid.props('showFavorite')).toBe(true)
    expect(grid.props('showActions')).toBe(false)
    expect((grid.props('items') as (typeof ITEM)[])[0].rating).toBe(4)
  })

  it('模特视图不显示评分', () => {
    const wrapper = mountSection('model')
    expect(wrapper.findComponent({ name: 'MasonryGrid' }).props('showRating')).toBe(false)
    expect(wrapper.findComponent({ name: 'MasonryGrid' }).props('showFavorite')).toBe(false)
  })

  it('网格评分事件透传 inspiration_id 与值', () => {
    const wrapper = mountSection('blogger')
    wrapper.findComponent({ name: 'MasonryGrid' }).vm.$emit('rate', 'insp-1', 5)
    expect(wrapper.emitted('rate')?.[0]).toEqual(['insp-1', 5])
  })
})
