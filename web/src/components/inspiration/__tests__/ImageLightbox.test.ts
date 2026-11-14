/**
 * ImageLightbox 的图片 URL 解析。
 *
 * 素材库图片是 storage 下的相对路径，必须套 `/api/files/` 前缀；而出图候选图由
 * `/api/generate/runs/{id}/candidates/{name}` 直接返回。原先灯箱内部写死 getFileUrl，
 * 候选图会被拼成 `/api/files/api/generate/...` 而 404，所以加了 urlResolver 口子。
 */

import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import ImageLightbox from '../ImageLightbox.vue'

let wrapper: VueWrapper | null = null

/** jsdom 没有 PointerEvent 构造器时，用 Event + 自定义属性模拟 */
function pointerEvent(
  type: string,
  pointerId: number,
  clientX: number,
  clientY: number,
  pointerType = 'touch',
): PointerEvent {
  const event = new Event(type, { bubbles: true, cancelable: true }) as PointerEvent
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    clientX: { value: clientX },
    clientY: { value: clientY },
    pointerType: { value: pointerType },
    button: { value: 0 },
  })
  return event
}

function wrapEl(): HTMLElement {
  return document.body.querySelector('.lightbox-img-wrap') as HTMLElement
}

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
  it('打开时暴露 dialog 语义与关闭/切换按钮的读屏标签', () => {
    mountBox({ imagePaths: ['images/a.jpg', 'images/b.jpg'] })

    const dialog = document.body.querySelector('[role="dialog"]')
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.getAttribute('aria-label')).toBe('图片预览')
    expect(document.body.querySelector('[aria-label="关闭图片预览"]')).toBeTruthy()
    expect(document.body.querySelector('[aria-label="下一张"]')).toBeTruthy()
  })

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

  it('双指手势按间距缩放图片（触摸端 pinch）', async () => {
    mountBox({ imagePath: 'images/a.jpg' })
    const target = wrapEl()

    target.dispatchEvent(pointerEvent('pointerdown', 1, 0, 0))
    target.dispatchEvent(pointerEvent('pointerdown', 2, 100, 0))
    window.dispatchEvent(pointerEvent('pointermove', 2, 200, 0))
    await nextTick()

    expect(document.body.querySelector('.zoom-level')?.textContent).toBe('200%')
  })
})
