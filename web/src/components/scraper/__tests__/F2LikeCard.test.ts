/**
 * F2LikeCard（采集我的喜欢）测试：主页链接的回填/保存、可用性门控与提交参数，
 * 以及「每次最多翻多少条」（like_max_counts / f2 的 `-o`）的保存与透传。
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
/** a-input-number 桩：number 型 v-model，渲染成 <input type="number">（带 .f2l-max 类便于定位） */
const inputNumberStub = defineComponent({
  name: 'AInputNumber',
  props: { modelValue: { type: Number, default: 0 } },
  emits: ['update:modelValue', 'input'],
  render() {
    return h('input', {
      class: 'f2l-max',
      type: 'number',
      value: this.modelValue,
      onInput: (e: Event) => {
        const value = Number((e.target as HTMLInputElement).value)
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
/** a-checkbox 桩：渲染真实 input[type=checkbox]，便于断言开关对提交参数的影响 */
const checkboxStub = defineComponent({
  name: 'ACheckbox',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  render() {
    return h('label', [
      h('input', {
        type: 'checkbox',
        checked: this.modelValue,
        onChange: (e: Event) =>
          this.$emit('update:modelValue', (e.target as HTMLInputElement).checked),
      }),
      this.$slots.default?.(),
    ])
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
    collect_root: 'C:/f2/Download/douyin/collection',
    collect_available: false,
    collect_reason: '未配置「我的主页链接」：收藏列表只有本人可见',
    like_max_counts: 0,
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

async function mountCard(status = makeStatus(), mode: 'like' | 'collection' = 'like') {
  mocks.get.mockResolvedValue({ data: status })
  const wrapper = mount(F2LikeCard, {
    props: { mode },
    global: {
      stubs: {
        'a-card': cardStub,
        'a-button': buttonStub,
        'a-input': inputStub,
        'a-input-number': inputNumberStub,
        'a-spin': spinStub,
        'a-link': linkStub,
        'a-checkbox': checkboxStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

/** 卡片里三个按钮：保存主页链接 / 保存翻页条数 / 提交（按文案定位，避免下标脆断） */
function buttons(wrapper: VueWrapper, submitText = '采集我的喜欢') {
  const all = wrapper.findAll('button')
  const byText = (text: string) => all.find((b) => b.text().includes(text))
  return {
    save: byText('保存')!,
    saveMax: all.filter((b) => b.text().includes('保存'))[1],
    submit: byText(submitText)!,
  }
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
        register_bloggers: true,
        like_max_counts: 0, // 缺省全量（显式传 0，避免被后端配置顶掉）
      },
    })
    expect(wrapper.emitted('submitted')).toBeTruthy()
  })

  it('「同时登记穿搭博主」开关默认开，取消勾选后按 false 下发', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
      }),
    )
    mocks.post.mockResolvedValue({ data: { task_id: 62, message: '已提交' } })

    const checkbox = wrapper.find('.f2l-options input[type="checkbox"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)

    await checkbox.setValue(false)
    await buttons(wrapper).submit.trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        mode: 'like',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        register_bloggers: false,
        like_max_counts: 0,
      },
    })
  })

  // ── 「每次最多翻多少条」（like_max_counts / f2 的 `-o`）──

  it('翻页条数从状态回填（0=全量）', async () => {
    const wrapper = await mountCard(makeStatus({ like_max_counts: 150 }))

    expect((wrapper.find('.f2l-max').element as HTMLInputElement).value).toBe('150')
  })

  it('保存翻页条数：PUT 到 f2-like-max-counts 并更新状态', async () => {
    const wrapper = await mountCard()
    mocks.put.mockResolvedValue({
      data: { message: '已保存：每次最多翻 150 条点赞（增量模式）', like_max_counts: 150 },
    })

    await wrapper.find('.f2l-max').setValue('150')
    await buttons(wrapper).saveMax.trigger('click')
    await flushPromises()

    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-like-max-counts', null, {
      params: { max_counts: 150, persist: true },
    })
    expect((wrapper.find('.f2l-max').element as HTMLInputElement).value).toBe('150')
  })

  it('提交时把翻页条数带上（增量模式真的下发到后端）', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
        like_max_counts: 200,
      }),
    )
    mocks.post.mockResolvedValue({ data: { task_id: 63, message: '已提交' } })

    await buttons(wrapper).submit.trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        mode: 'like',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        register_bloggers: true,
        like_max_counts: 200,
      },
    })
  })

  it('翻页条数非法（负数/空）时按 0=全量下发，不把非法值发给后端', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
      }),
    )
    mocks.post.mockResolvedValue({ data: { task_id: 64, message: '已提交' } })

    await wrapper.find('.f2l-max').setValue('-30')
    await buttons(wrapper).submit.trigger('click')
    await flushPromises()

    const params = (mocks.post.mock.calls[0][2] as { params: Record<string, unknown> }).params
    expect(params.like_max_counts).toBe(0)
  })

  it('收藏模式：按 mode=collection 提交，文案与可用性走收藏字段', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        collect_available: true,
        collect_reason: '已配置「我的主页链接」，可采集我的收藏（抖音收藏列表）',
      }),
      'collection',
    )
    mocks.post.mockResolvedValue({ data: { task_id: 71, message: '已提交' } })

    expect(wrapper.text()).toContain('采集我的收藏')
    // 点赞可用、收藏不可用时不能被放行：可用性必须读 collect_* 字段
    expect(wrapper.find('.f2l-status').text()).toContain('可采集我的收藏')

    await buttons(wrapper, '采集我的收藏').submit.trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        mode: 'collection',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        register_bloggers: true,
        like_max_counts: 0,
      },
    })
    expect(wrapper.emitted('submitted')).toBeTruthy()
  })

  it('收藏模式：收藏不可用时提交按钮禁用（不借用点赞的可用性）', async () => {
    const wrapper = await mountCard(
      makeStatus({
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        like_available: true,
        like_reason: '已配置「我的主页链接」，可采集我的喜欢（点赞作品）',
        collect_available: false,
        collect_reason: '未配置「我的主页链接」：收藏列表只有本人可见',
      }),
      'collection',
    )

    expect(wrapper.find('.f2l-status').text()).toContain('收藏列表只有本人可见')
    expect(buttons(wrapper, '采集我的收藏').submit.attributes('disabled')).toBeDefined()
  })
})
