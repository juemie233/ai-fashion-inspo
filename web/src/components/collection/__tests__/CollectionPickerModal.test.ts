/**
 * 「加入合集」弹窗的位置回归：宽度只能走 `:width`，不能写成裸 `style`。
 *
 * 2026-10-01 用户报「素材详情的加入合集弹窗跑到网页最左面」。根因在 Arco 的属性透传：
 * - `:width` / `:modal-style` 会被合并进 `.arco-modal`（Arco 的 mergedModalStyle）；
 * - 裸 `style` 则由 `$attrs` 透传到组件内层第一个元素 **`.arco-modal-container`**——
 *   它是 `position: fixed; left: 0; width: 100%` 的全屏容器（见 Arco modal.js 的
 *   mergeProps(..., $attrs)）。被写上 `width: 480px` 后整层容器只剩左侧 480px 宽，
 *   里面的弹窗再怎么居中也只是「在这 480px 里居中」，于是贴在网页最左边。
 *
 * 所以这里用**真实的 Modal** 渲染（不用桩，否则测的是桩而不是 Arco 的行为），断言：
 * 容器内联样式里没有宽度、`.arco-modal` 上才有宽度。谁把它改回裸 style，这条就会红。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { Modal } from '@arco-design/web-vue'

import CollectionPickerModal from '../CollectionPickerModal.vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

/** 打开弹窗时组件会拉一次合集列表（GET /collections） */
function mountModal() {
  mocks.get.mockResolvedValue({ data: [] })
  return mount(CollectionPickerModal, {
    props: { visible: true, inspirationIds: ['insp-1'] },
    global: {
      // 只注册真实的 a-modal：它是本次回归的对象；其余 Arco 组件与定位无关，打桩即可
      components: { 'a-modal': Modal },
      stubs: { 'a-spin': true, 'a-button': true, 'a-input': true, 'a-empty': true },
    },
  })
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('CollectionPickerModal 位置', () => {
  it('宽度落在 .arco-modal 上，全屏容器不被改窄（否则弹窗会贴到最左）', async () => {
    mountModal()
    await flushPromises()

    // 裸 style 会透传到这个容器上 —— 它就是「弹窗跑到最左」的元凶
    const containerEl = document.querySelector('.arco-modal-container')
    const modalEl = document.querySelector('.arco-modal')
    expect(containerEl).toBeTruthy()
    expect(modalEl).toBeTruthy()

    // 关键回归：容器是 position: fixed; left: 0; width: 100% 的全屏层，内联样式里不能有宽度
    expect(containerEl?.getAttribute('style') ?? '').not.toMatch(/width/i)
    // 宽度应当由 :width 传给真正的弹窗元素
    expect(modalEl?.getAttribute('style') ?? '').toMatch(/width:\s*480px/i)
  })

  it('没有合集的空状态照样渲染（弹窗本体可用）', async () => {
    mountModal()
    await flushPromises()

    // 弹窗内容被 Teleport 到 body，所以断言看 document.body 而不是组件根节点
    expect(document.body.textContent ?? '').toContain('加入合集')
    expect(document.querySelector('.arco-modal')).toBeTruthy()
  })
})
