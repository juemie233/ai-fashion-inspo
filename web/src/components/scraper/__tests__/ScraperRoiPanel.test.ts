/**
 * ScraperRoiPanel（采集 ROI）测试：入库率列点表头排序——第一次点击升序、再次点击降序。
 *
 * 为什么单列这条：Arco 表头排序的默认点击循环是「升 → 降 → **取消排序**」，第三次
 * 点击会把排序清掉；本面板用受控 `sortOrder` 改成严格升降互切（点第三次回到升序）。
 * 这个差异只看代码看不出来，只能靠用例锁住。
 */

/* eslint-disable vue/one-component-per-file -- 单文件内聚 Arco 桩组件，按引用查找更稳 */

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ScraperRoiPanel from '../ScraperRoiPanel.vue'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/api/client', () => ({ default: mocks }))

/** 入库率列（含排序配置）的最小形状 */
interface SortableColumn {
  dataIndex: string
  sortable?: {
    sortDirections: string[]
    sortOrder: string
    sorter?: (
      a: Record<string, unknown>,
      b: Record<string, unknown>,
      extra: { direction: 'ascend' | 'descend' },
    ) => number
  }
}

function makeRoi() {
  return {
    days: 180,
    by_keyword: [
      {
        keyword: 'JK制服',
        tasks: 7,
        found: 512,
        added: 301,
        add_rate: 58.8,
        attributed: 0,
        approved: 0,
        rejected: 0,
        pending: 0,
        approved_rate: null,
      },
    ],
    by_author: [
      {
        name: '里香',
        channel: 'f2',
        platform: 'douyin',
        tasks: null,
        found: null,
        added: null,
        add_rate: null,
        imported: 512,
        approved: 300,
        rejected: 12,
        pending: 200,
        approved_rate: 96.2,
      },
    ],
    coverage: {
      tasks_scanned: 47,
      multi_keyword_tasks: 0,
      keyword_attributed: 0,
      f2_materials: 512,
      notes: [],
    },
  }
}

const cardStub = defineComponent({
  name: 'ACard',
  render() {
    return h('div', [
      h('div', { class: 'card-title' }, this.$slots.title?.()),
      h('div', { class: 'card-body' }, this.$slots.default?.()),
    ])
  },
})
const buttonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', this.$slots.default?.())
  },
})
const spinStub = defineComponent({
  name: 'ASpin',
  render() {
    return h('div', this.$slots.default?.())
  },
})
/** 只保留「透传 columns / 抛出 sorterChange」两个能力，排序逻辑本身不依赖 Arco 内部实现 */
const tableStub = defineComponent({
  name: 'ATable',
  props: { columns: { type: Array, default: () => [] } },
  emits: ['sorterChange'],
  render() {
    return h('div', { class: 'table-stub' })
  },
})
const radioGroupStub = defineComponent({
  name: 'ARadioGroup',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue', 'change'],
  render() {
    return h('div', this.$slots.default?.())
  },
})
const passthroughStub = (name: string) =>
  defineComponent({
    name,
    render() {
      return h('div', this.$slots.default?.())
    },
  })

async function mountExpanded(): Promise<VueWrapper> {
  mocks.get.mockResolvedValue({ data: makeRoi() })
  const wrapper = mount(ScraperRoiPanel, {
    global: {
      stubs: {
        'a-card': cardStub,
        'a-button': buttonStub,
        'a-spin': spinStub,
        'a-table': tableStub,
        'a-radio-group': radioGroupStub,
        'a-radio': passthroughStub('ARadio'),
        'a-select': passthroughStub('ASelect'),
        'a-option': passthroughStub('AOption'),
        'a-tag': passthroughStub('ATag'),
        'a-alert': passthroughStub('AAlert'),
      },
    },
  })
  // 看板默认折叠；点击标题展开（展开后有 30ms 延迟触发加载）
  await wrapper.find('.card-title > div').trigger('click')
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 60))
  await flushPromises()
  return wrapper
}

/** 取当前渲染表格的入库率列 */
function addRateColumn(wrapper: VueWrapper): SortableColumn {
  const columns = wrapper.findComponent({ name: 'ATable' }).props('columns') as SortableColumn[]
  return columns.find((c) => c.dataIndex === 'add_rate')!
}

/** 模拟点击表头排序（Arco 抛出的方向：升 → 降 → 空字符串） */
async function clickSorter(wrapper: VueWrapper, direction: string) {
  wrapper.findComponent({ name: 'ATable' }).vm.$emit('sorterChange', 'add_rate', direction)
  await flushPromises()
}

describe('ScraperRoiPanel · 入库率点表头排序', () => {
  beforeEach(() => {
    mocks.get.mockReset()
  })

  it('入库率列带排序配置，初始未排序', async () => {
    const wrapper = await mountExpanded()
    const col = addRateColumn(wrapper)

    expect(col.sortable).toBeTruthy()
    expect(col.sortable!.sortDirections).toEqual(['ascend', 'descend'])
    expect(col.sortable!.sortOrder).toBe('')
  })

  it('点击一次升序、再点一次降序', async () => {
    const wrapper = await mountExpanded()

    await clickSorter(wrapper, 'ascend')
    expect(addRateColumn(wrapper).sortable!.sortOrder).toBe('ascend')

    await clickSorter(wrapper, 'descend')
    expect(addRateColumn(wrapper).sortable!.sortOrder).toBe('descend')
  })

  it('第三次点击回到升序而不是取消排序（Arco 默认循环会清空排序）', async () => {
    const wrapper = await mountExpanded()

    await clickSorter(wrapper, 'ascend')
    await clickSorter(wrapper, 'descend')
    await clickSorter(wrapper, '') // Arco 第二次之后抛出的「取消」信号

    expect(addRateColumn(wrapper).sortable!.sortOrder).toBe('ascend')
  })

  it('表格让非入库率列的排序事件原样透传，不影响入库率排序状态', async () => {
    const wrapper = await mountExpanded()

    await clickSorter(wrapper, 'ascend')
    wrapper.findComponent({ name: 'ATable' }).vm.$emit('sorterChange', 'found', 'descend')
    await flushPromises()

    expect(addRateColumn(wrapper).sortable!.sortOrder).toBe('ascend')
  })

  it('列排序函数把「—」（发现数为 0）排到末尾，升降序都成立', async () => {
    const wrapper = await mountExpanded()
    const sorter = addRateColumn(wrapper).sortable!.sorter!
    const rows = [{ add_rate: 58.8 }, { add_rate: null }, { add_rate: 12.5 }]

    expect([...rows].sort((a, b) => sorter(a, b, { direction: 'ascend' }))).toEqual([
      { add_rate: 12.5 },
      { add_rate: 58.8 },
      { add_rate: null },
    ])
    expect([...rows].sort((a, b) => sorter(a, b, { direction: 'descend' }))).toEqual([
      { add_rate: 58.8 },
      { add_rate: 12.5 },
      { add_rate: null },
    ])
  })

  it('切到「按博主」维度后，博主表的入库率列同样可排序', async () => {
    const wrapper = await mountExpanded()

    wrapper.findComponent({ name: 'ARadioGroup' }).vm.$emit('update:modelValue', 'author')
    await flushPromises()

    const col = addRateColumn(wrapper)
    expect(col.sortable!.sortDirections).toEqual(['ascend', 'descend'])
    expect(col.sortable!.sortOrder).toBe('')
  })
})
