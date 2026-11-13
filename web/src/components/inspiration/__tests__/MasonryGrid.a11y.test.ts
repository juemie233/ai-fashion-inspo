/** 瀑布流网格的列表语义。 */

import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import MasonryGrid from '../MasonryGrid.vue'

const CardStub = defineComponent({
  name: 'InspirationCard',
  template: '<div class="card-stub" />',
})

describe('MasonryGrid 无障碍', () => {
  it('网格与单元分别暴露 list / listitem 语义', () => {
    const wrapper = mount(MasonryGrid, {
      props: {
        items: [
          { id: 'a', file_path: 'a.jpg', media_type: 'image', is_favorite: false, tags: [] },
          { id: 'b', file_path: 'b.jpg', media_type: 'image', is_favorite: false, tags: [] },
        ] as never,
      },
      global: { plugins: [ArcoVue], stubs: { InspirationCard: CardStub } },
    })

    const grid = wrapper.find('.masonry-grid')
    expect(grid.attributes('role')).toBe('list')
    expect(grid.attributes('aria-label')).toBe('素材列表')
    expect(wrapper.findAll('.masonry-cell').every((c) => c.attributes('role') === 'listitem')).toBe(
      true,
    )
    wrapper.unmount()
  })
})
