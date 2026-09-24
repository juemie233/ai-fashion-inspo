/** 未匹配人脸 tab 的悬停大图预览回归用例。
 *
 * 关注：明细网格里鼠标停留超过 delay 后，浮层（Teleport 到 body 的
 * .hover-preview-layer）必须出现；移出必须消失。大图地址取 file_path（图片素材）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import FaceUnmatchedTab from '../FaceUnmatchedTab.vue'
import type { DetectionItem } from '@/api/faceScan'

const item: DetectionItem = {
  detection_id: 1,
  inspiration_id: 'insp-1',
  confidence: 0.8,
  file_path: 'images/2026/09/a.jpg',
  thumbnail_path: 'thumbnails/2026/09/a.webp',
  media_type: 'image',
}

function mountTab() {
  return mount(FaceUnmatchedTab, {
    props: {
      items: [item],
      loading: false,
      page: 1,
      total: 1,
      checked: new Set<number>(),
      assignOptions: [],
      assignLoading: false,
      assigning: false,
      filterOption: () => true,
      assignKind: 'blogger',
      assignPersonId: undefined,
    },
  })
}

describe('FaceUnmatchedTab 悬停大图预览', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('鼠标停留超过延迟后弹出浮层，移出后消失', async () => {
    const wrapper = mountTab()
    const trigger = wrapper.find('.hover-preview-trigger')
    expect(trigger.exists()).toBe(true)
    // 网格缩略图用 thumbnail_path
    expect(wrapper.find('.hover-preview-trigger img').attributes('src')).toBe(
      '/api/files/thumbnails/2026/09/a.webp',
    )

    await trigger.trigger('mouseenter')
    expect(document.body.querySelector('.hover-preview-layer')).toBeNull()

    vi.advanceTimersByTime(300)
    await wrapper.vm.$nextTick()
    const layer = document.body.querySelector('.hover-preview-layer')
    expect(layer).not.toBeNull()
    // 浮层里的大图必须指向原图 file_path
    expect(layer?.querySelector('img')?.getAttribute('src')).toBe('/api/files/images/2026/09/a.jpg')

    await trigger.trigger('mouseleave')
    await wrapper.vm.$nextTick()
    expect(document.body.querySelector('.hover-preview-layer')).toBeNull()
  })
})
