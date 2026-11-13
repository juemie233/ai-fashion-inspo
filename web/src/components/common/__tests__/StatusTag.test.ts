/** 任务状态标签：中文文案与未知状态回退。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import StatusTag from '../StatusTag.vue'

function mountTag(status: string) {
  return mount(StatusTag, {
    props: { status },
    global: { plugins: [ArcoVue] },
  })
}

describe('StatusTag', () => {
  it.each([
    ['pending', '排队中'],
    ['running', '运行中'],
    ['success', '已完成'],
    ['completed', '已完成'],
    ['failed', '失败'],
    ['cancelled', '已取消'],
  ])('%s → %s', (status, label) => {
    expect(mountTag(status).text()).toBe(label)
  })

  it('未知状态原样展示，不吞成空标签', () => {
    expect(mountTag('weird-status').text()).toBe('weird-status')
  })
})
