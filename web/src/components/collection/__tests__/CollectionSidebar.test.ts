/**
 * 合集列表侧栏（CollectionSidebar）：两级树展示与交互回抛测试。
 *
 * 这个组件是合集页的左侧导航，之前没有单测（覆盖率 0%）。它本身不持有合集数据，
 * 只负责三件事：
 * 1. 把「一级 + 其直属二级」渲染成两级树，副标题按「收藏夹数 / 素材数」拼；
 * 2. 折叠一级节点（抖音同步出来的二级收藏夹可能很多，默认展开、可收起）；
 * 3. 把选中、拖拽、上移下移、新建等意图通过 emit 回抛给视图，排序状态不在这里维护。
 *
 * 因为视图侧依赖这些 emit 驱动重排接口，事件类型/参数写错会导致「拖了没反应」，
 * 所以这里同时锁住事件名与参数。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import type { CollectionOut } from '@/api/collections'
import CollectionSidebar from '../CollectionSidebar.vue'

type CollectionOverrides = Partial<CollectionOut> & Pick<CollectionOut, 'id' | 'name'>

/** 造一条合集；默认是「无子夹、无素材」的一级手动合集，按需覆盖字段。 */
function makeCollection(overrides: CollectionOverrides): CollectionOut {
  return {
    description: null,
    kind: 'manual',
    position: 0,
    parent_id: null,
    auto_source: null,
    child_count: 0,
    cover_inspiration_id: null,
    cover_thumbnail_path: null,
    item_count: 0,
    query_json: null,
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z',
    ...overrides,
  }
}

/**
 * 两级混排的数据：一级 roo1 下挂两个二级；roo2 是智能合集；roo3 是空分类。
 * 与视图侧一致：collections 是一级/二级混排的全量列表，childrenByParent 单独给二级。
 */
function fixture() {
  const child11 = makeCollection({ id: 11, name: '白裙收藏夹', parent_id: 1, item_count: 3 })
  const child12 = makeCollection({ id: 12, name: '通勤收藏夹', parent_id: 1, item_count: 5 })
  const root1 = makeCollection({
    id: 1,
    name: '日常合集',
    child_count: 2,
    item_count: 10,
  })
  const root2 = makeCollection({
    id: 2,
    name: '高分灵感',
    kind: 'smart',
    item_count: null,
  })
  const root3 = makeCollection({ id: 3, name: '空分类', item_count: 0 })

  const collections = [root1, child11, child12, root2, root3]
  const childrenByParent = new Map<number, CollectionOut[]>([[1, [child11, child12]]])
  return { collections, childrenByParent }
}

interface MountOptions {
  collections?: CollectionOut[]
  childrenByParent?: Map<number, CollectionOut[]>
  currentId?: number | null
  loading?: boolean
  dragOverId?: number | null
}

function mountSidebar(options: MountOptions = {}) {
  const { collections, childrenByParent } = fixture()
  return mount(CollectionSidebar, {
    props: {
      collections: options.collections ?? collections,
      childrenByParent: options.childrenByParent ?? childrenByParent,
      currentId: options.currentId ?? null,
      loading: options.loading ?? false,
      dragOverId: options.dragOverId ?? null,
    },
    global: { plugins: [ArcoVue] },
  })
}

type SidebarWrapper = ReturnType<typeof mountSidebar>

/** 一级节点（非二级） */
function rootItems(wrapper: SidebarWrapper) {
  return wrapper.findAll('.collection-item:not(.child)')
}

function childItems(wrapper: SidebarWrapper) {
  return wrapper.findAll('.collection-item.child')
}

function findButtonByText(wrapper: SidebarWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('CollectionSidebar 两级树展示', () => {
  it('渲染全部一级与二级节点，头部显示合集总数', () => {
    const wrapper = mountSidebar()

    expect(wrapper.text()).toContain('合集（5）')
    expect(rootItems(wrapper)).toHaveLength(3)
    expect(childItems(wrapper)).toHaveLength(2)
    expect(wrapper.text()).toContain('日常合集')
    expect(wrapper.text()).toContain('白裙收藏夹')
    expect(wrapper.text()).toContain('通勤收藏夹')
    expect(wrapper.text()).toContain('高分灵感')
    expect(wrapper.text()).toContain('空分类')
  })

  it('副标题按类型拼装：收藏夹数+素材数 / 智能合集 / 纯素材数', () => {
    const wrapper = mountSidebar()

    // 一级有 2 个二级、自身 10 个素材
    expect(wrapper.text()).toContain('2 个收藏夹 · 10 个素材')
    // 智能合集不显示素材数（进入合集才懒计算）
    expect(wrapper.text()).toContain('智能合集')
    // 无子夹的手动合集只报素材数
    expect(wrapper.text()).toContain('0 个素材')
  })

  it('一级有子夹但没有直属素材时只报收藏夹数（避免被读成空夹）', () => {
    const onlyChild = makeCollection({ id: 41, name: '子夹', parent_id: 4, item_count: 2 })
    const root4 = makeCollection({ id: 4, name: '只有子夹', child_count: 1, item_count: 0 })
    const wrapper = mountSidebar({
      collections: [root4, onlyChild],
      childrenByParent: new Map<number, CollectionOut[]>([[4, [onlyChild]]]),
    })

    expect(wrapper.text()).toContain('1 个收藏夹')
    expect(wrapper.text()).not.toContain('0 个素材')
  })

  it('智能合集与普通合集用不同图标字区分', () => {
    const wrapper = mountSidebar()
    const kinds = rootItems(wrapper).map((item) => item.find('.collection-kind').text())

    expect(kinds).toEqual(['集', '智', '集'])
  })
})

describe('CollectionSidebar 折叠', () => {
  it('有子夹的一级节点显示折叠开关，点击收起/展开二级', async () => {
    const wrapper = mountSidebar()
    const root1 = rootItems(wrapper)[0]
    const toggle = root1.find('.collapse-toggle')

    expect(toggle.text()).toBe('▾')
    expect(childItems(wrapper)).toHaveLength(2)

    await toggle.trigger('click')
    expect(toggle.text()).toBe('▸')
    expect(childItems(wrapper)).toHaveLength(0)

    await toggle.trigger('click')
    expect(toggle.text()).toBe('▾')
    expect(childItems(wrapper)).toHaveLength(2)
  })

  it('没有子夹的一级节点只占位、不显示可点折叠开关', () => {
    const wrapper = mountSidebar()
    const root3 = rootItems(wrapper)[2]

    expect(root3.find('.collapse-toggle').classes()).toContain('placeholder')
    expect(root3.find('.collapse-toggle').text()).toBe('')
  })
})

describe('CollectionSidebar 选中与拖拽', () => {
  it('currentId 命中一级/二级时对应节点高亮', async () => {
    const wrapper = mountSidebar({ currentId: 1 })

    expect(rootItems(wrapper)[0].classes()).toContain('active')
    expect(childItems(wrapper)[0].classes()).not.toContain('active')

    await wrapper.setProps({ currentId: 11 })

    expect(rootItems(wrapper)[0].classes()).not.toContain('active')
    expect(childItems(wrapper)[0].classes()).toContain('active')
  })

  it('点击节点回抛 update:currentId', async () => {
    const wrapper = mountSidebar()

    await rootItems(wrapper)[1].trigger('click')
    await childItems(wrapper)[0].trigger('click')

    expect(wrapper.emitted('update:currentId')).toEqual([[2], [11]])
  })

  it('拖拽相关事件按原参数回抛，dragOverId 命中时显示拖拽高亮', async () => {
    const wrapper = mountSidebar({ dragOverId: 2 })
    const root2 = rootItems(wrapper)[1]

    expect(root2.classes()).toContain('drag-over')
    expect(rootItems(wrapper)[0].classes()).not.toContain('drag-over')

    await rootItems(wrapper)[0].trigger('dragstart')
    await rootItems(wrapper)[0].trigger('dragover')
    await rootItems(wrapper)[0].trigger('dragleave')
    await rootItems(wrapper)[0].trigger('drop')

    expect(wrapper.emitted('drag-start')).toEqual([[1]])
    expect(wrapper.emitted('update:dragOverId')).toEqual([[1], [null]])
    expect(wrapper.emitted('drop')).toEqual([[1]])
  })
})

describe('CollectionSidebar 排序与新建入口', () => {
  it('上移/下移按钮在边界禁用，点击回抛 move 方向', async () => {
    const wrapper = mountSidebar()
    const roots = rootItems(wrapper)

    expect(roots[0].find('[aria-label="上移合集"]').classes()).toContain('arco-btn-disabled')
    expect(roots[0].find('[aria-label="下移合集"]').classes()).not.toContain('arco-btn-disabled')
    expect(roots[2].find('[aria-label="下移合集"]').classes()).toContain('arco-btn-disabled')

    await roots[0].find('[aria-label="下移合集"]').trigger('click')
    await roots[1].find('[aria-label="上移合集"]').trigger('click')

    expect(wrapper.emitted('move')).toEqual([
      [1, 'down'],
      [2, 'up'],
    ])
  })

  it('头部新建按钮回抛 create-manual / create-smart', async () => {
    const wrapper = mountSidebar()

    await findButtonByText(wrapper, '＋ 手动')!.trigger('click')
    await findButtonByText(wrapper, '智能')!.trigger('click')

    expect(wrapper.emitted('create-manual')).toHaveLength(1)
    expect(wrapper.emitted('create-smart')).toHaveLength(1)
  })

  it('手动一级节点下有「新建二级收藏夹」入口，智能合集没有', async () => {
    const wrapper = mountSidebar()
    const childCreateEntries = wrapper.findAll('.child-create')

    // root1 与 root3 是手动合集，都有入口；智能 root2 没有
    expect(childCreateEntries).toHaveLength(2)

    await childCreateEntries[0].trigger('click')
    expect(wrapper.emitted('create-child')).toEqual([[1]])
  })
})

describe('CollectionSidebar 空状态与加载中', () => {
  it('无合集且不在加载中时显示空状态引导', () => {
    const wrapper = mountSidebar({
      collections: [],
      childrenByParent: new Map<number, CollectionOut[]>(),
    })

    expect(wrapper.text()).toContain('暂无合集，点击上方按钮新建')
    expect(wrapper.findAll('.collection-item')).toHaveLength(0)
  })

  it('加载中隐藏空状态，spin 进入 loading', () => {
    const wrapper = mountSidebar({
      collections: [],
      childrenByParent: new Map<number, CollectionOut[]>(),
      loading: true,
    })

    expect(wrapper.findComponent({ name: 'Spin' }).props('loading')).toBe(true)
    expect(wrapper.find('.arco-spin-loading').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('暂无合集')
  })
})
