/**
 * AnalysisQueueOverview（标签分析 · 分析队列总览）测试：覆盖「按指定数量分析」入口——
 * 数量输入框默认值、按钮文案与实际提交数量，以及「超过未分析数」「无可分析素材」两个边界。
 *
 * 为什么单列这条：原来只有「分析全部未分析」一个按钮，全库十几万条未打标素材
 * 一点就是几十小时。数量入口一旦夹取逻辑写错（例如把 500 提交成 50，或把 0 条
 * 也提交成 1 条），用户很难从界面上发现，因此把提交数量锁死。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AnalysisQueueOverview from '../AnalysisQueueOverview.vue'

vi.mock('@/api/inspirations', () => ({ getFileUrl: (path: string) => `/files/${path}` }))

const buttonStub = defineComponent({
  name: 'AButton',
  props: { disabled: { type: Boolean, default: false } },
  emits: ['click'],
  render() {
    return h(
      'button',
      { disabled: this.disabled, onClick: () => this.$emit('click') },
      this.$slots.default?.(),
    )
  },
})

/** 桩件模拟用户输入：直接把输入值作为 update:modelValue 抛出（真实 Arco 的 max 夹取不参与） */
const inputNumberStub = defineComponent({
  name: 'AInputNumber',
  props: {
    modelValue: { type: Number, default: 0 },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  render() {
    return h('input', {
      type: 'number',
      value: this.modelValue,
      disabled: this.disabled,
      onInput: (e: Event) =>
        this.$emit('update:modelValue', Number((e.target as HTMLInputElement).value)),
    })
  },
})

const progressStub = defineComponent({ name: 'AProgress', render: () => h('div') })
const cardStub = defineComponent({
  name: 'ACard',
  render() {
    return h('div', this.$slots.default?.())
  },
})
const alertStub = defineComponent({
  name: 'AAlert',
  render() {
    return h('div', this.$slots.default?.())
  },
})

/** 按未分析数量挂载队列总览（总数为「已分析 100 + 未分析 N」，保证统计自洽） */
function mountOverview(unanalyzed: number, analysisTasks: unknown[] = []): VueWrapper {
  return mount(AnalysisQueueOverview, {
    props: {
      queueStats: { total: 100 + unanalyzed, analyzed: 100, unanalyzed, failed: 0 },
      batchAnalyzing: false,
      analysisTasks: analysisTasks as never,
      activeAnalyses: {},
      pendingQueue: [],
      queuePaused: false,
    },
    global: {
      stubs: {
        'a-button': buttonStub,
        'a-input-number': inputNumberStub,
        'a-progress': progressStub,
        'a-card': cardStub,
        'a-alert': alertStub,
        StatusTag: true,
      },
    },
  })
}

/** 一条运行中的批量分析任务（用于验证行内「取消 / 暂停」入口） */
function runningTask() {
  return {
    id: 7,
    type: 'batch_analyze',
    status: 'running',
    progress: 0,
    total: 18259,
    done: 0,
    result: null,
    error: null,
    retry_count: 0,
    max_retries: 2,
    next_retry_at: null,
    created_at: '2026-09-20T01:13:25Z',
    updated_at: '2026-09-20T01:28:00Z',
  }
}

/** 「分析 N 个」按钮（与「分析全部未分析」按钮区分：后者文案里没有「个」） */
function countButton(wrapper: VueWrapper) {
  return wrapper
    .findAll('button')
    .find((b) => /^分析 \d+ 个$|^无可分析素材$/.test(b.text().trim()))!
}

describe('AnalysisQueueOverview · 按指定数量分析', () => {
  it('默认提交 50 个，按钮文案与提交数量一致', async () => {
    const wrapper = mountOverview(300)
    const button = countButton(wrapper)
    expect(button.text().trim()).toBe('分析 50 个')

    await button.trigger('click')
    expect(wrapper.emitted('analyzeCount')?.[0]).toEqual([50])
  })

  it('输入任意数量后按该数量提交', async () => {
    const wrapper = mountOverview(3000)
    await wrapper.find('input[type="number"]').setValue('200')

    expect(countButton(wrapper).text().trim()).toBe('分析 200 个')

    await countButton(wrapper).trigger('click')
    expect(wrapper.emitted('analyzeCount')?.[0]).toEqual([200])
  })

  it('输入数量超过未分析数时夹到实际数量，不提交空批次', async () => {
    const wrapper = mountOverview(30)
    await wrapper.find('input[type="number"]').setValue('500')

    expect(countButton(wrapper).text().trim()).toBe('分析 30 个')

    await countButton(wrapper).trigger('click')
    expect(wrapper.emitted('analyzeCount')?.[0]).toEqual([30])
  })

  it('没有未分析素材时输入框与按钮均禁用', () => {
    const wrapper = mountOverview(0)

    expect(wrapper.find('input[type="number"]').attributes('disabled')).toBeDefined()
    const button = countButton(wrapper)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text().trim()).toBe('无可分析素材')
  })
})

describe('AnalysisQueueOverview · 运行中任务的取消入口', () => {
  /** 按文案定位按钮 */
  function buttonByText(wrapper: VueWrapper, text: string) {
    return wrapper.findAll('button').find((b) => b.text().trim() === text)!
  }

  it('运行中的任务同时提供「取消」与「暂停」，取消发出 cancelTask', async () => {
    const wrapper = mountOverview(300, [runningTask()])

    const cancel = buttonByText(wrapper, '取消')
    expect(cancel).toBeTruthy()
    expect(buttonByText(wrapper, '⏸ 暂停')).toBeTruthy()

    await cancel.trigger('click')
    expect(wrapper.emitted('cancelTask')?.[0]?.[0]).toMatchObject({
      id: 7,
      status: 'running',
    })
  })

  it('取消按钮不只出现在 pending 行（运行中也能取消，不再只能干等）', () => {
    const wrapper = mountOverview(300, [runningTask()])
    const cancelButtons = wrapper.findAll('button').filter((b) => b.text().trim() === '取消')
    expect(cancelButtons).toHaveLength(1)
  })
})
