/** 批量操作栏评分入口：选择/清除评分都通过 batch-update 的 rating 提交。 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: vi.fn(async () => ({ items: [], total: 0 })) },
}))

import BatchActionBar from '../BatchActionBar.vue'

function mountBar() {
  return mount(BatchActionBar, {
    props: { count: 3, allSelected: false, tagOptions: [] },
    attachTo: document.body,
    global: { plugins: [ArcoVue] },
  })
}

function clickBodyButton(text: string) {
  const button = [...document.body.querySelectorAll('button')].find((el) =>
    el.textContent?.includes(text),
  )
  expect(button, `找不到按钮：${text}`).toBeTruthy()
  button!.click()
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('BatchActionBar 评分', () => {
  it('「清除评分」提交 rating=0', async () => {
    const wrapper = mountBar()
    await flushPromises()

    const rateEntry = wrapper.findAll('button').find((b) => b.text() === '评分')
    expect(rateEntry).toBeTruthy()
    await rateEntry!.trigger('click')
    await flushPromises()

    clickBodyButton('清除评分')
    await flushPromises()

    expect(wrapper.emitted('update')?.[0]?.[0]).toEqual({ rating: 0 })
    wrapper.unmount()
  })
})
