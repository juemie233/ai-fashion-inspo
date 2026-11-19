/** 侧栏收藏中心入口：浏览分组出现「收藏中心」，且当前页选中态正确。 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  routeName: 'favorites' as string,
  connect: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({
    name: mocks.routeName,
    path: mocks.routeName === 'favorites' ? '/favorites' : '/',
    meta: { title: '收藏中心' },
    query: {},
  }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/composables/useWebSocket', () => ({
  connectTaskWebSocket: mocks.connect,
}))

import AppLayout from '@/components/layout/AppLayout.vue'

/** jsdom 没有 matchMedia；布局用它在 ≤1024 收起侧栏 */
function stubMatchMedia() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

function mountLayout() {
  return mount(AppLayout, {
    global: {
      plugins: [ArcoVue, createPinia()],
      mocks: { $router: { push: vi.fn() } },
      stubs: { SchemaVersionBanner: true },
    },
    slots: { default: '<div class="page-slot" />' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  stubMatchMedia()
})

describe('侧栏收藏中心入口', () => {
  it('浏览分组显示「收藏中心」，当前页菜单项选中', async () => {
    const wrapper = mountLayout()
    await wrapper.vm.$nextTick()

    const text = wrapper.text()
    expect(text).toContain('收藏合集')
    expect(text).toContain('收藏中心')

    const selected = wrapper.find('.arco-menu-selected')
    expect(selected.exists()).toBe(true)
    expect(selected.text()).toContain('收藏中心')
  })

  it('不在收藏中心时该入口不选中', () => {
    mocks.routeName = 'home'
    const wrapper = mountLayout()
    const selected = wrapper.find('.arco-menu-selected')
    expect(selected.exists()).toBe(true) // 素材库选中
    expect(selected.text()).not.toContain('收藏中心')
  })
})
