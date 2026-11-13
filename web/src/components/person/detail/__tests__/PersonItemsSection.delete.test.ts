/** 人物详情「全部」网格的删除入口：只对博主开放，事件透传素材 id。 */

import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import PersonItemsSection from '../PersonItemsSection.vue'

const MasonryStub = defineComponent({
  name: 'MasonryGrid',
  props: {
    items: { type: Array, default: () => [] },
    showDelete: { type: Boolean, default: false },
  },
  emits: ['delete'],
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

function mountSection(kind: 'blogger' | 'model', unbindMode = false) {
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
      unbindMode,
      unbindSelectedIds: new Set<string>(),
      unbindSubmitting: false,
      clearingAll: false,
      isCurrentPageAllSelected: false,
      hasImageItems: true,
    },
    global: { plugins: [ArcoVue], stubs: { MasonryGrid: MasonryStub } },
  })
}

describe('PersonItemsSection 删除素材', () => {
  it('博主「全部」视图显示删除按钮（作品视图/注销模式不显示）', () => {
    expect(mountSection('blogger').findComponent({ name: 'MasonryGrid' }).props('showDelete')).toBe(
      true,
    )
    expect(
      mountSection('blogger', true).findComponent({ name: 'MasonryGrid' }).props('showDelete'),
    ).toBe(false)
  })

  it('模特视图不显示删除按钮', () => {
    expect(mountSection('model').findComponent({ name: 'MasonryGrid' }).props('showDelete')).toBe(
      false,
    )
  })

  it('网格 delete 事件透传素材 id', () => {
    const wrapper = mountSection('blogger')
    wrapper.findComponent({ name: 'MasonryGrid' }).vm.$emit('delete', 'insp-1')
    expect(wrapper.emitted('delete-inspiration')?.[0]).toEqual(['insp-1'])
  })
})
