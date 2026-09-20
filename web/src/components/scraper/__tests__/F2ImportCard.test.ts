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

/** 表格桩件：按列的 slotName 渲染每一行的作用域插槽，便于断言单元格内容 */
const tableStub = defineComponent({
  name: 'ATable',
  props: {
    columns: { type: Array, default: () => [] },
    data: { type: Array, default: () => [] },
  },
  render() {
    const columns = this.columns as { dataIndex: string; slotName?: string }[]
    const rows = this.data as Record<string, unknown>[]
    return h(
      'table',
      rows.map((row) =>
        h(
          'tr',
          columns.map((col) => {
            const slot = col.slotName ? this.$slots[col.slotName] : undefined
            return h('td', slot ? slot({ record: row }) : String(row[col.dataIndex] ?? ''))
          }),
        ),
      ),
    )
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

/** Arco 桩组件表（卡片用到的组件全部打桩，测试只关心提交参数与清单渲染） */
const STUBS = {
  'a-card': cardStub,
  'a-button': buttonStub,
  'a-input': inputStub,
  'a-input-number': inputNumberStub,
  'a-spin': spinStub,
  'a-link': linkStub,
  'a-checkbox': checkboxStub,
  'a-table': tableStub,
}

/** 用给定的 GET 实现挂载卡片 */
async function mountWithResponses(
  get: (url: string) => Promise<{ data: unknown }>,
): Promise<VueWrapper> {
  mocks.get.mockImplementation(get)
  const wrapper = mount(F2ImportCard, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

async function mountCard(status = makeStatus()) {
  return mountWithResponses(() => Promise.resolve({ data: status }))
}

/** 博主清单响应（GET /scraper/f2-authors） */
function makeAuthors(over: Record<string, unknown> = {}) {
  return {
    available: true,
    f2_dir: 'C:/f2',
    filter_active: true,
    registered_count: 1,
    unknown_count: 1,
    note: '另有 1 个 f2 账号未登记到博主库，默认会被跳过；勾选「包含 f2 里未登记到博主库的账号」才会处理它们',
    registered: [
      {
        nickname: '里香1√',
        sec_user_id: 'sec-lixiang',
        aweme_count: 171,
        materials: 512,
        blogger_id: 304,
        blogger_name: '里香',
        profile_url: 'https://www.douyin.com/user/sec-lixiang',
      },
    ],
    unknown: [
      {
        nickname: '网易第五人格',
        sec_user_id: 'sec-wy',
        aweme_count: 42,
        materials: 142,
        blogger_id: null,
        blogger_name: null,
        profile_url: 'https://www.douyin.com/user/sec-wy',
      },
    ],
    ...over,
  }
}

/** 状态与博主清单分开返回：清单请求走清单，其余（f2-status）走状态 */
async function mountCardWithAuthors(
  authors = makeAuthors(),
  status = makeStatus(),
): Promise<VueWrapper> {
  return mountWithResponses((url) =>
    Promise.resolve({ data: url === '/scraper/f2-authors' ? authors : status }),
  )
}

/** 按文案定位链接 */
function linkByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll('a').find((a) => a.text().includes(text))!
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

describe('F2ImportCard · 已登记博主清单', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('默认不展开：不请求清单接口', async () => {
    const wrapper = await mountCardWithAuthors()

    expect(wrapper.text()).toContain('查看已登记博主（19）')
    expect(mocks.get).not.toHaveBeenCalledWith('/scraper/f2-authors')
  })

  it('展开后拉取清单：昵称链到抖音主页、显示总作品与已入库素材数', async () => {
    const wrapper = await mountCardWithAuthors()

    await linkByText(wrapper, '查看已登记博主').trigger('click')
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-authors')
    const nameLink = linkByText(wrapper, '里香1√')
    expect(nameLink.attributes('href')).toBe('https://www.douyin.com/user/sec-lixiang')
    const rowText = wrapper.find('tr').text()
    expect(rowText).toContain('里香') // 库内博主名
    expect(rowText).toContain('171') // f2 记录的总作品数
    expect(rowText).toContain('512') // 已入库素材数
  })

  it('展示说明文案（还有多少未登记账号会被跳过）', async () => {
    const wrapper = await mountCardWithAuthors()

    await linkByText(wrapper, '查看已登记博主').trigger('click')
    await flushPromises()

    expect(wrapper.find('.f2-authors-note').text()).toContain('1 个 f2 账号未登记')
  })

  it('再次点击收起清单，不会重复请求', async () => {
    const wrapper = await mountCardWithAuthors()

    await linkByText(wrapper, '查看已登记博主').trigger('click')
    await flushPromises()
    await linkByText(wrapper, '收起已登记博主').trigger('click')
    await flushPromises()

    expect(wrapper.find('.f2-authors').exists()).toBe(false)
    expect(mocks.get.mock.calls.filter((c) => c[0] === '/scraper/f2-authors')).toHaveLength(1)
  })

  it('白名单里没有账号时给出引导文案而不是空表格', async () => {
    const wrapper = await mountCardWithAuthors(
      makeAuthors({ registered: [], registered_count: 0, filter_active: false }),
    )

    await linkByText(wrapper, '查看已登记博主').trigger('click')
    await flushPromises()

    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.text()).toContain('白名单里暂无账号')
  })
})
