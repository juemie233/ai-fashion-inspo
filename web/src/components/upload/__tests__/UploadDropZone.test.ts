/**
 * UploadDropZone 回归测试：页面文案「单次最多 N 个」必须与队列上限同源。
 *
 * 历史缺陷：DropZone 文案与 UploadView 的校验常量各自写死 500，
 * 调整上限时极易只改一处（校验放行 1500、文案仍写 500）。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 测试桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import UploadDropZone from '../UploadDropZone.vue'
import { MAX_UPLOAD_QUEUE_SIZE } from '@/constants/upload'

// ===== Arco 组件最小桩（带显式 name，供 VTU 按引用查找） =====
const inputStub = defineComponent({
  name: 'AInput',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  render() {
    return h('input')
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', { type: 'button' }, this.$slots.default?.())
  },
})

function mountZone() {
  return mount(UploadDropZone, {
    props: { urlInput: '', urlImporting: false, hasQueue: false },
    global: { stubs: { 'a-input': inputStub, 'a-button': buttonStub } },
  })
}

describe('UploadDropZone（上传主区域）', () => {
  it('单次上传队列上限为 1500', () => {
    expect(MAX_UPLOAD_QUEUE_SIZE).toBe(1500)
  })

  it('文案中的上限引用共享常量，改上限时自动跟随', () => {
    const text = mountZone().text()
    expect(text).toContain(`单次最多 ${MAX_UPLOAD_QUEUE_SIZE} 个`)
    expect(text).toContain('单次最多 1500 个')
  })

  it('选择文件后把文件数组抛给父组件', async () => {
    const wrapper = mountZone()
    const fileInput = wrapper.findAll('input[type="file"]')[0]
    Object.defineProperty(fileInput.element, 'files', {
      value: [new File(['x'], 'a.jpg', { type: 'image/jpeg' })],
    })
    await fileInput.trigger('change')
    expect(wrapper.emitted('filesSelected')?.[0]?.[0]).toHaveLength(1)
  })
})
