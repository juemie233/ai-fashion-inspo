/**
 * 人物管理页「优先评分」入口回归：只在穿搭博主 tab 出现，点击跳 /person-rating。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  query: { kind: 'blogger' } as Record<string, unknown>,
  push: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.query }),
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))

vi.mock('@/components/person/PersonListSection.vue', () => ({
  default: { name: 'PersonListSection', template: '<div class="person-list-stub" />' },
}))

import PersonView from '../PersonView.vue'

function mountView() {
  return mount(PersonView, { global: { plugins: [ArcoVue] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.query = { kind: 'blogger' }
})

describe('PersonView 优先评分入口', () => {
  it('穿搭博主 tab 显示「优先评分」并跳转 /person-rating', async () => {
    const wrapper = mountView()
    await wrapper.vm.$nextTick()

    const entry = wrapper.findAll('button').find((b) => b.text() === '优先评分')
    expect(entry).toBeTruthy()
    await entry!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/person-rating')
  })

  it('职业模特 tab 不显示「优先评分」（模特无作品评分业务）', () => {
    mocks.query = { kind: 'model' }
    const wrapper = mountView()

    expect(wrapper.text()).not.toContain('优先评分')
    // 人脸库扫描入口仍在
    expect(wrapper.text()).toContain('人脸库扫描')
  })
})
