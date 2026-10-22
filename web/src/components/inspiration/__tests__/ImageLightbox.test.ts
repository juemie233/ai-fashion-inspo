/**
 * ImageLightbox 的图片 URL 解析。
 *
 * 素材库图片是 storage 下的相对路径，必须套 `/api/files/` 前缀；而出图候选图由
 * `/api/generate/runs/{id}/candidates/{name}` 直接返回。原先灯箱内部写死 getFileUrl，
 * 候选图会被拼成 `/api/files/api/generate/...` 而 404，所以加了 urlResolver 口子。
 */

import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import ImageLightbox from '../ImageLightbox.vue'

let wrapper: VueWrapper | null = null

function mountBox(props: Record<string, unknown>): void {
  wrapper = mount(ImageLightbox, {
    props: { show: true, ...props },
    attachTo: document.body,
    // 布尔桩 + 渲染默认插槽：灯箱的图片与按钮都在 a-spin / a-button 插槽内
    global: { renderStubDefaultSlot: true, stubs: { 'a-spin': true, 'a-button': true } },
  })
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('ImageLightbox 图片 URL 解析', () => {
  it('缺省给素材相对路径套上 /api/files 前缀', () => {
    mountBox({ imagePath: 'images/2026-10/a b.webp' })
    const img = document.body.querySelector('.lightbox-img')
    expect(img?.getAttribute('src')).toBe('/api/files/images/2026-10/a%20b.webp')
  })

  it('传 urlResolver 时按调用方给的 URL 走，不再二次加前缀', () => {
    const url = '/api/generate/runs/13/candidates/01_seed-129068865.png'
    mountBox({ imagePath: url, urlResolver: (p: string) => p })
    const img = document.body.querySelector('.lightbox-img')
    expect(img?.getAttribute('src')).toBe(url)
  })
})
