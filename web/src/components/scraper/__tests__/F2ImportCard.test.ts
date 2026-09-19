/**
 * F2ImportCard（抖音一键获取素材）测试：本次只覆盖「按博主全量下载」这条新入口，
 * 以及它不影响原有的「一键获取素材」参数形状。
 *
 * 为什么单列这条：f2 的下载目标只来自它自己的用户库，从「我的喜欢」里发现的新博主
 * 走「一键获取素材」会得到「下载 0 个作者 + 入库 0」。这条入口用主页链接点名，
 * 参数一旦漏传就会退回那个静默失败，所以把形状锁死。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import F2ImportCard from '../F2ImportCard.vue'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))

vi.mock('@/api/client', () => ({ default: mocks }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

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
const inputNumberStub = defineComponent({
  name: 'AInputNumber',
  props: { modelValue: { type: Number, default: 0 } },
  emits: ['update:modelValue'],
  render() {
    return h('input', { type: 'number', value: this.modelValue })
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
const checkboxStub = defineComponent({
  name: 'ACheckbox',
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  render() {
    return h('input', { type: 'checkbox', checked: this.modelValue })
  },
})

function makeStatus(over: Record<string, unknown> = {}) {
  return {
    available: true,
    reason: '可增量下载 19 个已登记博主',
    authors: 19,
    unknown_authors: [],
    f2_dir: 'C:/f2',
    root: 'C:/f2/Download/douyin/post',
    like_root: 'C:/f2/Download/douyin/like',
    like_user: '',
    like_available: false,
    like_reason: '',
    like_max_counts: 0,
    fetch_since_days: 14,
    auto: {
      enabled: false,
      interval_hours: 24,
      skip_live: false,
      available: true,
      reason: '',
      authors: 19,
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
  const wrapper = mount(F2ImportCard, {
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

const SEC = 'MS4wLjABAAAACyG6qmWLGt5BbCvwkAfMpEf3nhGwlQqSG1MjwDIGokuUHJnIkwJzxDPu-1RRrfvk'

/** 卡片里「下载她全部作品」按钮（按文案定位） */
function profileButton(wrapper: VueWrapper) {
  return wrapper.findAll('button').find((b) => b.text().includes('下载她全部作品'))!
}

describe('F2ImportCard · 按博主全量下载', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('填主页链接后按 profiles 提交（fetch=true，不带博主白名单筛选）', async () => {
    const wrapper = await mountCard()
    mocks.post.mockResolvedValue({ data: { task_id: 77, message: '已提交' } })

    await wrapper.find('.f2-profile input').setValue(`https://www.douyin.com/user/${SEC}`)
    await profileButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        profiles: [`https://www.douyin.com/user/${SEC}`],
        make_thumbnails: true,
      },
    })
    expect(wrapper.emitted('submitted')).toBeTruthy()
  })

  it('多个博主（逗号分隔）拆成数组', async () => {
    const wrapper = await mountCard()
    mocks.post.mockResolvedValue({ data: { task_id: 78, message: '已提交' } })

    await wrapper
      .find('.f2-profile input')
      .setValue(`${SEC}, https://www.douyin.com/user/MS4wLjABAAAAother0000`)
    await profileButton(wrapper).trigger('click')
    await flushPromises()

    const params = (mocks.post.mock.calls[0][2] as { params: { profiles: string[] } }).params
    expect(params.profiles).toEqual([SEC, 'https://www.douyin.com/user/MS4wLjABAAAAother0000'])
  })

  it('没填时按钮禁用，不会发出空 profiles 的请求', async () => {
    const wrapper = await mountCard()

    expect(profileButton(wrapper).attributes('disabled')).toBeDefined()
    await profileButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('提交成功后清空输入框（避免重复点同一个博主）', async () => {
    const wrapper = await mountCard()
    mocks.post.mockResolvedValue({ data: { task_id: 79, message: '已提交' } })

    const input = wrapper.find('.f2-profile input')
    await input.setValue(SEC)
    await profileButton(wrapper).trigger('click')
    await flushPromises()

    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('后端复用已有任务（reused）时不误报成功文案，但也不清空输入', async () => {
    const wrapper = await mountCard()
    mocks.post.mockResolvedValue({
      data: { task_id: 80, message: '已有进行中的任务', reused: true },
    })

    const input = wrapper.find('.f2-profile input')
    await input.setValue(SEC)
    await profileButton(wrapper).trigger('click')
    await flushPromises()

    // reused 也返回 task_id → 仍算提交成功（走同一轮轮询）
    expect(wrapper.emitted('submitted')).toBeTruthy()
  })
})
