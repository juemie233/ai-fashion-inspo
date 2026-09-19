/**
 * F2LikeCard（采集我的喜欢）测试：主页链接的回填/保存、可用性门控与提交参数。
 *
 * 这条入口与「一键获取素材」共用同一个后端点，差别只在 `mode=like` 与主页链接；
 * 一旦参数漏传就会去拉博主主页（不该发生），所以这里把参数形状锁死。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import F2LikeCard from '../F2LikeCard.vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const cardStub = defineComponent({
  name: 'ACard',
  render() {
    return h('div', this.$slots.default?.())
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', this.$slots.default?.())
  },
})
const inputStub = defineComponent({
  name: 'AInput',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue', 'input'],
  render() {
    return h('input', {
      value: this.modelValue,
      onInput: (e: Event) => {
        const value = (e.target as HTMLInputElement).value
        this.$emit('update:modelValue', value)
        this.$emit('input', value)
      },
    })
  },
})
const spinStub = defineComponent({
  name: 'ASpin',
  render() {
    return h('span', this.$slots.default?.())
  },
})
const linkStub = defineComponent({
  name: 'ALink',
  render() {
    return h('a', this.$slots.default?.())
  },
})

function makeStatus(over: Record<string, unknown> = {}) {
  return {
    available: false,
    reason: 'f2 用户库为空',
    authors: 0,
    unknown_authors: [],
    f2_dir: 'C:/f2',
    root: 'C:/f2/Download/douyin/post',
    like_root: 'C:/f2/Download/douyin/like',
    like_user: '',
    like_available: false,
    like_reason: '未配置「我的主页链接」：点赞列表只有本人可见',
    fetch_since_days: 14,
    auto: {
      enabled: false,
      interval_hours: 24,
      skip_live: false,
      available: false,
      reason: '',
      authors: 0,
      last_task_at: null,
      next_due_at: null,
      running_task_id: null,
      running: null,
    },
    ...over,
  }
}

async function mountCard(status = makeStatus()) {
  mocks.get.mockResolvedValue({ data: status })
  const wrapper = mount(F2LikeCard, {
    global: {
      stubs: {
        'a-card': cardStub,
        'a-button': buttonStub,
        'a-input': inputStub,
        'a-spin': spinStub,
        'a-link': linkStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

/** 卡片里两个按钮：0=保存主页链接，1=采集我的喜欢 */
function buttons(wrapper: VueWrapper) {
  const all = wrapper.findAll('button')
  return { save: all[0], submit: all[1] }
}

describe('F2LikeCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('主页链接从状态回填；未配置时提交按钮禁用', async () => {
    const wrapper = await mountCard(
      makeStatus({ like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme' }),
    )

    expect((wrapper.find('input').element as HTMLInputElement).value).toBe(
      'https://www.douyin.com/user/MS4wLjABAAAAme',
    )
    expect(wrapper.find('.f2l-status').text()).toContain('未配置「我的主页链接」')
    expect(buttons(wrapper).submit.attributes('disabled')).toBeDefined()
  })

  it('保存主页链接：PUT 到 f2-like-user 并回读状态', async () => {
    const wrapper = await mountCard()
    mocks.put.mockResolvedValue({
      data: { message: '已保存', like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme' },
    })
    mocks.get.mockResolvedValue({
      data: makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
        like_reason: '已配置「我的主页链接」，可采集我的喜欢（点赞作品）',
      }),
    })

    await wrapper.find('input').setValue('MS4wLjABAAAAme')
    await buttons(wrapper).save.trigger('click')
    await flushPromises()

    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-like-user', null, {
      params: { like_user: 'MS4wLjABAAAAme', persist: true },
    })
    expect(wrapper.find('.f2l-status').text()).toContain('可采集我的喜欢')
  })

  it('提交走 like 模式并带上主页链接，成功后通知父组件刷新历史', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
        like_reason: '已配置「我的主页链接」，可采集我的喜欢（点赞作品）',
      }),
    )
    mocks.post.mockResolvedValue({ data: { task_id: 61, message: '已提交' } })

    const submit = buttons(wrapper).submit
    expect(submit.attributes('disabled')).toBeUndefined()
    await submit.trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        mode: 'like',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
      },
    })
    expect(wrapper.emitted('submitted')).toBeTruthy()
  })
})
