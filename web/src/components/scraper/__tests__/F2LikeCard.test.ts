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
/** a-popconfirm 桩：渲染默认插槽 + 一个「确认」按钮（点它等于用户点了确认） */
const popconfirmStub = defineComponent({
  name: 'APopconfirm',
  emits: ['ok'],
  render() {
    return h('span', [
      this.$slots.default?.(),
      h('button', { class: 'popconfirm-ok', onClick: () => this.$emit('ok') }, '确认'),
    ])
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
/** a-checkbox 桩：渲染真实 input[type=checkbox]。
 *  - 独立使用（卡片里的「同时登记穿搭博主」、弹窗里的「全选」）：布尔 v-model / model-value
 *  - 在 a-checkbox-group 内（收藏夹清单）：从注入的组上下文取选中态，点击时切成员
 */
const checkboxStub = defineComponent({
  name: 'ACheckbox',
  inject: { group: { from: 'arcoCheckboxGroup', default: null } },
  props: {
    modelValue: { type: Boolean, default: false },
    value: { type: String, default: '' },
  },
  emits: ['update:modelValue', 'change'],
  render() {
    const group = this.group as { value: string[]; toggle: (v: string) => void } | null
    if (group) {
      const checked = (group.value ?? []).includes(this.value)
      return h('label', { class: 'f2l-folder' }, [
        h('input', {
          type: 'checkbox',
          value: this.value,
          checked,
          onChange: () => group.toggle(this.value),
        }),
        this.$slots.default?.(),
      ])
    }
    return h('label', [
      h('input', {
        type: 'checkbox',
        checked: this.modelValue,
        onChange: (e: Event) => {
          const checked = (e.target as HTMLInputElement).checked
          this.$emit('update:modelValue', checked)
          this.$emit('change', checked)
        },
      }),
      this.$slots.default?.(),
    ])
  },
})
/** a-checkbox-group 桩：受控数组 + 注入组上下文，供上面的 checkbox 桩成员切换 */
const checkboxGroupStub = defineComponent({
  name: 'ACheckboxGroup',
  provide() {
    // 用 getter 惰性读 props：provide() 的执行时机早于 props 就绪，直接取值会拿到 undefined
    const self = this as unknown as {
      modelValue?: string[]
      $emit: (event: string, ...args: unknown[]) => void
    }
    return {
      arcoCheckboxGroup: {
        get value() {
          return self.modelValue ?? []
        },
        toggle(v: string) {
          const next = [...(self.modelValue ?? [])]
          const index = next.indexOf(v)
          if (index >= 0) next.splice(index, 1)
          else next.push(v)
          self.$emit('update:modelValue', next)
        },
      },
    }
  },
  props: { modelValue: { type: Array, default: () => [] } },
  emits: ['update:modelValue'],
  render() {
    return h('div', { class: 'f2l-group' }, this.$slots.default?.())
  },
})
/** a-modal 桩：visible 为真时渲染内容，并给一个「确定」按钮触发 ok */
const modalStub = defineComponent({
  name: 'AModal',
  props: {
    visible: { type: Boolean, default: false },
    title: { type: String, default: '' },
    okText: { type: String, default: '确定' },
  },
  emits: ['update:visible', 'ok'],
  render() {
    if (!this.visible) return null
    return h('div', { class: 'f2l-modal' }, [
      h('div', { class: 'f2l-modal-title' }, this.title),
      this.$slots.default?.(),
      h('button', { class: 'f2l-modal-ok', onClick: () => this.$emit('ok') }, this.okText),
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

async function mountCard(
  status = makeStatus(),
  mode: 'like' | 'collection' = 'like',
  collects: { id: string; name: string; total: number }[] | null = null,
) {
  // 收藏模式的卡片会额外拉收藏夹清单（先扫描），这里按 URL 分别返回
  mocks.get.mockImplementation((url: string) =>
    Promise.resolve({
      data:
        url === '/scraper/f2-collects'
          ? {
              folders: collects ?? [],
              total_folders: (collects ?? []).length,
              total_works: (collects ?? []).reduce((sum, f) => sum + f.total, 0),
              cookie_source: 'conf/app.yaml',
            }
          : status,
    }),
  )
  const wrapper = mount(F2LikeCard, {
    props: { mode },
    global: {
      stubs: {
        'a-card': cardStub,
        'a-button': buttonStub,
        'a-popconfirm': popconfirmStub,
        'a-input': inputStub,
        'a-input-number': inputNumberStub,
        'a-spin': spinStub,
        'a-link': linkStub,
        'a-checkbox': checkboxStub,
        'a-checkbox-group': checkboxGroupStub,
        'a-modal': modalStub,
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
    // 收藏夹选择是本地记忆的：不清掉会串到下一个用例
    localStorage.clear()
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

    // 收藏卡片有两个动作：「先扫描收藏夹」（主）与「一键下载全部收藏」（老口径）；
    // 后者会连同无关的收藏夹一起收进来，所以现在包了一层确认——必须先点确认才发请求
    await buttons(wrapper, '一键下载全部收藏').submit.trigger('click')
    await flushPromises()
    expect(mocks.post).not.toHaveBeenCalled()

    await wrapper.find('button.popconfirm-ok').trigger('click')
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
    expect(buttons(wrapper, '先扫描收藏夹').submit.attributes('disabled')).toBeDefined()
    expect(buttons(wrapper, '一键下载全部收藏').submit.attributes('disabled')).toBeDefined()
  })

  // ── 「先扫描、后下载」：夹默认全选，勾掉的夹一件都不下 ──

  /** 收藏可用 + 已配主页链接（否则卡片上的按钮是禁用的，点了不会发请求） */
  const READY_COLLECT_STATUS = () =>
    makeStatus({
      like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
      collect_available: true,
      collect_reason: '已配置「我的主页链接」，可采集我的收藏（抖音收藏列表）',
    })

  const FOLDERS = [
    { id: '111', name: '秘书OL', total: 96 },
    { id: '222', name: '股票', total: 1 },
    { id: '333', name: '过膝袜短袜JK', total: 334 },
  ]

  it('先扫描：拉收藏夹清单 → 弹窗列出全部夹且默认全选', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-collects')
    const modal = wrapper.find('.f2l-modal')
    expect(modal.exists()).toBe(true)
    expect(modal.text()).toContain('秘书OL（96 件）')
    expect(modal.text()).toContain('股票（1 件）')
    // 默认全选：三个夹的复选框都是选中的
    const boxes = modal.findAll('.f2l-folder input[type="checkbox"]')
    expect(boxes).toHaveLength(3)
    expect(boxes.every((b) => (b.element as HTMLInputElement).checked)).toBe(true)
    expect(modal.text()).toContain('3 / 3 个夹')
    expect(modal.text()).toContain('431 / 431 件')
  })

  it('勾掉不想要的夹：只把剩下的 collect_ids 下发给后端', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)
    mocks.post.mockResolvedValue({ data: { task_id: 81, message: '已提交' } })

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()
    // 取消勾选「股票」
    const boxes = wrapper.findAll('.f2l-folder input[type="checkbox"]')
    await boxes[1].setValue(false)
    await wrapper.find('.f2l-modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: {
        fetch: true,
        mode: 'collection',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
        register_bloggers: true,
        like_max_counts: 0,
        collect_ids: ['111', '333'],
      },
    })
    expect(wrapper.emitted('submitted')).toBeTruthy()
    // 提交后弹窗关闭
    expect(wrapper.find('.f2l-modal').exists()).toBe(false)
  })

  it('一个夹都没勾：不下发任何任务（避免误点成「全下」）', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()
    // 点「全选 / 全不选」→ 全不选（原本是全选状态，点一下即清空）
    await wrapper.find('.f2l-collect-head input[type="checkbox"]').setValue(false)
    await flushPromises()
    await wrapper.find('.f2l-modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.post).not.toHaveBeenCalled()
    expect(wrapper.find('.f2l-modal').exists()).toBe(true) // 弹窗留着让用户改
  })

  it('扫描结果为空时不打开弹窗（提示改用「一键下载全部收藏」）', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', [])

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()

    expect(wrapper.find('.f2l-modal').exists()).toBe(false)
    expect(mocks.post).not.toHaveBeenCalled()
  })

  // ── 本地记住上次的收藏夹选择（只留最近一次）──

  it('第一次扫描：默认全选，并说明这次的选择会被记住', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()

    const modal = wrapper.find('.f2l-modal')
    expect(
      modal
        .findAll('.f2l-folder input[type="checkbox"]')
        .every((b) => (b.element as HTMLInputElement).checked),
    ).toBe(true)
    expect(modal.text()).toContain('默认全选')
    expect(modal.text()).toContain('记住')
    expect(localStorage.getItem('f2-collect-folders')).toBeNull() // 只扫描不写记录
  })

  it('确认下载后记住选择：再次扫描时恢复上次的勾选', async () => {
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)
    mocks.post.mockResolvedValue({ data: { task_id: 91, message: '已提交' } })

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()
    // 取消勾选「股票」后确认下载
    await wrapper.findAll('.f2l-folder input[type="checkbox"]')[1].setValue(false)
    await wrapper.find('.f2l-modal-ok').trigger('click')
    await flushPromises()

    const saved = JSON.parse(localStorage.getItem('f2-collect-folders') as string)
    expect(saved.ids).toEqual(['111', '333'])

    // 再扫描一次：应当恢复上次的选择（「股票」仍未勾），而不是回到默认全选
    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()

    const boxes = wrapper.findAll('.f2l-folder input[type="checkbox"]')
    expect((boxes[0].element as HTMLInputElement).checked).toBe(true)
    expect((boxes[1].element as HTMLInputElement).checked).toBe(false)
    expect((boxes[2].element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.find('.f2l-modal').text()).toContain('已恢复上次的选择（2 个夹）')
  })

  it('上次勾的夹已不存在：只恢复仍存在的，并在提示里点出失效数量', async () => {
    localStorage.setItem(
      'f2-collect-folders',
      JSON.stringify({ ids: ['111', '999'], savedAt: '2026-09-22T00:00:00.000Z' }),
    )
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()

    const boxes = wrapper.findAll('.f2l-folder input[type="checkbox"]')
    expect((boxes[0].element as HTMLInputElement).checked).toBe(true) // 111 仍在
    expect((boxes[1].element as HTMLInputElement).checked).toBe(false) // 222 上次没勾
    expect(wrapper.find('.f2l-modal').text()).toContain('上次勾的 1 个夹已不存在')
  })

  it('只扫描不下载：不覆盖上次的选择', async () => {
    localStorage.setItem(
      'f2-collect-folders',
      JSON.stringify({ ids: ['111'], savedAt: '2026-09-22T00:00:00.000Z' }),
    )
    const wrapper = await mountCard(READY_COLLECT_STATUS(), 'collection', FOLDERS)

    await buttons(wrapper, '先扫描收藏夹').submit.trigger('click')
    await flushPromises()
    // 改一下勾选但直接关掉弹窗（不确认）
    await wrapper.findAll('.f2l-folder input[type="checkbox"]')[1].setValue(true)
    await flushPromises()

    expect(mocks.post).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('f2-collect-folders') as string).ids).toEqual(['111'])
  })
})
