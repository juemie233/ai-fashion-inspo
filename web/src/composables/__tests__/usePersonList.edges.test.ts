/**
 * 人物列表逻辑（`usePersonList`）补测（原 69.80%）。
 *
 * 既有 `usePersonList.test.ts` / `usePersonList.sorting.test.ts` 覆盖了 URL 持久化/页码修正/删除翻页/排序核心；
 * 本文件补：
 * - 新建/编辑弹窗、搜索回车、展开行、跳详情的上下文携带；
 * - 删除后「本页只剩一条且不在第一页」回退一页、纳入追踪、热门排行/缺失计数（含失败静默）；
 * - 两级排序剩余的边界：表头清除、次排序提升为主、次排序方向翻转、两列已满替换；
 * - 表格列的 render 分支（头像/自动登记标签/多平台徽标/评分样本不足/时间列/操作按钮与确认框）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, reactive } from 'vue'
import { mount } from '@vue/test-utils'
import { Button, Popconfirm } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  routeQuery: {} as Record<string, string>,
  replace: vi.fn(),
  push: vi.fn(),
  bloggerStats: vi.fn(),
  bloggerRemove: vi.fn(),
  bloggerPromote: vi.fn(),
  bloggerTop: vi.fn(),
  modelRemove: vi.fn(),
  modelTop: vi.fn(),
  fetchMissingProfiles: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  storeHolder: null as unknown,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))
vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchStats: mocks.bloggerStats,
    remove: mocks.bloggerRemove,
    promote: mocks.bloggerPromote,
    fetchTop: mocks.bloggerTop,
  },
  modelsApi: {
    remove: mocks.modelRemove,
    fetchTop: mocks.modelTop,
  },
  fetchMissingProfiles: mocks.fetchMissingProfiles,
}))
vi.mock('@/api/inspirations', () => ({ getFileUrl: (p: string) => `/files/${p}` }))
vi.mock('@/utils/format', () => ({
  formatDate: (v: unknown) => `date:${String(v)}`,
  renderTimeCell: (text: string) => `cell:${text}`,
}))
vi.mock('@/stores/persons', () => ({ usePersonsStore: () => mocks.storeHolder }))
vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { formatSortLabel, isRatingSort, usePersonList } from '../usePersonList'
import type { TableColumnData } from '@arco-design/web-vue'

type PersonLike = Record<string, unknown>

function person(over: PersonLike = {}): PersonLike {
  return {
    id: 1,
    name: '博主甲',
    platform: 'xiaohongshu',
    source: 'manual',
    avatar_path: null,
    face_registered: false,
    ip_location: '上海',
    inspiration_count: 8,
    rating_average: null,
    rated_count: 0,
    last_inspiration_at: '2026-11-20T10:00:00Z',
    created_at: '2026-11-01T10:00:00Z',
    ...over,
  }
}

function makeStore() {
  // 真实 Pinia store 会把 ref 解包；mock 用 reactive 对象模拟同样的「直接读写字段」形态
  return reactive({
    persons: [] as PersonLike[],
    page: 1,
    size: 20,
    total: 0,
    search: '',
    platform: '',
    source: '',
    sort: 'count' as 'newest' | 'name' | 'count' | 'rating' | 'rating_asc',
    order: 'desc' as 'asc' | 'desc',
    sort2: '' as '' | 'name' | 'count' | 'rating' | 'rating_asc',
    order2: 'desc' as 'asc' | 'desc',
    ratingMin: '' as number | '',
    load: vi.fn(async () => {}),
    setPage: vi.fn(async () => {}),
    reload: vi.fn(async () => {}),
  })
}

type StoreMock = ReturnType<typeof makeStore>

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function list(kind: 'blogger' | 'model' = 'blogger') {
  return usePersonList(kind)
}

function columnOf(columns: TableColumnData[], dataIndex: string): TableColumnData {
  const column = columns.find((c) => c.dataIndex === dataIndex)
  if (!column) throw new Error(`找不到列 ${dataIndex}`)
  return column
}

/** 把单列 render 结果挂载成组件，便于断言文本与交互 */
function renderCell(column: TableColumnData, record: PersonLike) {
  return mount(
    {
      render: () =>
        (column.render as unknown as (ctx: Record<string, unknown>) => unknown)({
          record,
          rowIndex: 0,
          column,
        }),
    },
    { global: { plugins: [] } },
  )
}

describe('usePersonList 补测', () => {
  let store: StoreMock

  beforeEach(() => {
    vi.clearAllMocks()
    store = makeStore()
    mocks.storeHolder = store
    for (const key of Object.keys(mocks.routeQuery)) delete mocks.routeQuery[key]
    mocks.bloggerStats.mockResolvedValue({ total: 3, items: [{ platform: 'douyin', count: 2 }] })
    mocks.bloggerRemove.mockResolvedValue({})
    mocks.bloggerPromote.mockResolvedValue({})
    mocks.bloggerTop.mockResolvedValue([person()])
    mocks.modelTop.mockResolvedValue([])
    mocks.fetchMissingProfiles.mockResolvedValue({ total: 4 })
  })

  describe('纯函数与弹窗状态', () => {
    it('formatSortLabel：评分方向编码在 sort 里，count 用 order 方向，未知值原样返回', () => {
      expect(formatSortLabel('rating', 'asc')).toBe('作品评分 ↓')
      expect(formatSortLabel('rating_asc', 'desc')).toBe('作品评分 ↑')
      expect(formatSortLabel('count', 'asc')).toBe('素材数 ↑')
      expect(formatSortLabel('count', 'desc')).toBe('素材数 ↓')
      expect(formatSortLabel('newest', 'desc')).toBe('最新创建')
      expect(formatSortLabel('unknown_field', 'asc')).toBe('unknown_field')
      expect(isRatingSort('rating')).toBe(true)
      expect(isRatingSort('rating_asc')).toBe(true)
      expect(isRatingSort('count')).toBe(false)
      expect(isRatingSort(null)).toBe(false)
    })

    it('openCreate / openEdit 控制表单与编辑对象', () => {
      const persons = list()
      persons.openEdit(person({ id: 9 }) as never)
      expect(persons.showForm.value).toBe(true)
      expect(persons.editingPerson.value?.id).toBe(9)

      persons.openCreate()
      expect(persons.showForm.value).toBe(true)
      expect(persons.editingPerson.value).toBeNull()
    })

    it('setExpandedGroupIds 原样保存展开行；搜索回车（非输入法）触发 reload', () => {
      const persons = list()
      persons.setExpandedGroupIds([3, 5])
      expect(persons.expandedGroupIds.value).toEqual([3, 5])

      persons.onSearchKeydown({ key: 'Enter', isComposing: false } as KeyboardEvent)
      expect(store.reload).toHaveBeenCalledTimes(1)

      persons.onSearchKeydown({ key: 'Enter', isComposing: true } as KeyboardEvent)
      persons.onSearchKeydown({ key: 'a', isComposing: false } as KeyboardEvent)
      expect(store.reload).toHaveBeenCalledTimes(1)
    })
  })

  it('挂载即恢复 URL 上下文，并加载列表/排行/缺失计数/统计', async () => {
    mocks.routeQuery.page = '2'
    mocks.routeQuery.q = '法式'
    store.total = 25 // 让第 2 页合法（maxPage=2），否则 loadAndSync 会按越界把页码拉回 1
    const Comp = defineComponent({
      setup() {
        usePersonList('blogger')
        return () => h('div', 'persons')
      },
    })
    mount(Comp)
    await flush()

    expect(store.page).toBe(2)
    expect(store.search).toBe('法式')
    expect(store.load).toHaveBeenCalled()
    expect(mocks.bloggerTop).toHaveBeenCalled()
    expect(mocks.fetchMissingProfiles).toHaveBeenCalled()
    expect(mocks.bloggerStats).toHaveBeenCalled()
  })

  describe('侧栏数据（排行/缺失/统计）', () => {
    it('loadTop：博主用 bloggersApi.fetchTop；失败静默保持旧值', async () => {
      const persons = list('blogger')
      await persons.loadTop()
      expect(mocks.bloggerTop).toHaveBeenCalledWith(5)
      expect(persons.topPersons.value).toHaveLength(1)

      mocks.bloggerTop.mockRejectedValueOnce(new Error('500'))
      await persons.loadTop()
      expect(persons.topPersons.value).toHaveLength(1) // 失败不覆盖旧数据
    })

    it('loadTop：模特用 modelsApi.fetchTop；空结果回退 []', async () => {
      mocks.modelTop.mockResolvedValueOnce(null)
      const persons = list('model')
      await persons.loadTop()
      expect(mocks.modelTop).toHaveBeenCalledWith(5)
      expect(persons.topPersons.value).toEqual([])
    })

    it('loadMissingCount 仅博主：成功回填总数；模特不发请求；失败静默', async () => {
      const blogger = list('blogger')
      await blogger.loadMissingCount()
      expect(blogger.missingTotal.value).toBe(4)

      mocks.fetchMissingProfiles.mockRejectedValueOnce(new Error('500'))
      await blogger.loadMissingCount()
      expect(blogger.missingTotal.value).toBe(4)

      const model = list('model')
      mocks.fetchMissingProfiles.mockClear()
      await model.loadMissingCount()
      expect(mocks.fetchMissingProfiles).not.toHaveBeenCalled()
    })

    it('platformCount 从统计项取数，未知平台为 0', async () => {
      const persons = list('blogger')
      await persons.loadBloggerStats()
      expect(persons.platformCount('douyin')).toBe(2)
      expect(persons.platformCount('weibo')).toBe(0)
    })
  })

  describe('删除 / 纳入追踪 / 跳转', () => {
    it('handleDelete：本页只剩一条且不在第一页时回退一页，否则刷新当前页', async () => {
      store.persons = [person()]
      store.page = 2
      const persons = list()
      await persons.handleDelete(person() as never)
      expect(mocks.bloggerRemove).toHaveBeenCalledWith(1)
      expect(mocks.message.success).toHaveBeenCalledWith('已删除穿搭博主「博主甲」')
      expect(store.setPage).toHaveBeenCalledWith(1)

      store.persons = [person(), person({ id: 2 })]
      store.page = 2
      await persons.handleDelete(person() as never)
      expect(store.load).toHaveBeenCalledWith(true)
    })

    it('handleDelete 失败：透出后端 detail，不回退页码', async () => {
      mocks.bloggerRemove.mockRejectedValueOnce({ response: { data: { detail: '还有素材关联' } } })
      store.persons = [person()]
      store.page = 2
      const persons = list()

      await persons.handleDelete(person() as never)

      expect(mocks.message.error).toHaveBeenCalledWith('还有素材关联')
      expect(store.setPage).not.toHaveBeenCalled()
    })

    it('handlePromote：提示追踪白名单前提并刷新；失败透出 detail', async () => {
      const persons = list()
      await persons.handlePromote(person({ source: 'auto_collect' }) as never)

      expect(mocks.bloggerPromote).toHaveBeenCalledWith(1)
      expect(mocks.message.success).toHaveBeenCalledWith(expect.stringContaining('纳入追踪'))
      expect(store.load).toHaveBeenCalledWith(true)

      mocks.bloggerPromote.mockRejectedValueOnce({ response: { data: { detail: '账号不存在' } } })
      await persons.handlePromote(person() as never)
      expect(mocks.message.error).toHaveBeenCalledWith('账号不存在')
    })

    it('goDetail 携带列表上下文 query', () => {
      store.page = 3
      store.search = ' 法式 '
      store.platform = 'douyin'
      const persons = list()

      persons.goDetail(person({ id: 7 }) as never)

      expect(mocks.push).toHaveBeenCalledWith({
        path: '/persons/7',
        query: expect.objectContaining({
          kind: 'blogger',
          page: '3',
          q: '法式',
          platform: 'douyin',
        }),
      })
    })
  })

  describe('两级排序边界', () => {
    it('表头清除主排序列且无次排序：回默认排序并重新加载', async () => {
      store.sort = 'rating'
      const persons = list()

      persons.onSorterChange('rating_average', null)
      await flush()

      expect(store.sort).toBe('count')
      expect(store.order).toBe('desc')
      expect(store.load).toHaveBeenCalled()
    })

    it('表头清除主排序列时把次排序提升为主排序', async () => {
      store.sort = 'count'
      store.sort2 = 'rating'
      const persons = list()

      persons.onSorterChange('inspiration_count', null)
      await flush()

      expect(store.sort).toBe('rating')
      expect(store.sort2).toBe('')
    })

    it('表头清除次排序列：只清空次排序', async () => {
      store.sort = 'count'
      store.sort2 = 'rating'
      const persons = list()

      persons.onSorterChange('rating_average', null)
      await flush()

      expect(store.sort2).toBe('')
      expect(store.order2).toBe('desc')
      expect(store.sort).toBe('count')
    })

    it('次排序方向翻转：count 翻转 order2，rating 在 rating/rating_asc 间切换', async () => {
      store.sort = 'name'
      store.sort2 = 'count'
      store.order2 = 'desc'
      const persons = list()
      persons.onSorterChange('inspiration_count', 'ascend')
      await flush()
      expect(store.order2).toBe('asc')

      store.sort = 'name'
      store.sort2 = 'rating'
      persons.onSorterChange('rating_average', 'ascend')
      await flush()
      expect(store.sort2).toBe('rating_asc')

      persons.onSorterChange('rating_average', 'ascend')
      await flush()
      expect(store.sort2).toBe('rating')
    })

    it('两列已满时点新列：新列替换次排序', async () => {
      store.sort = 'name'
      store.sort2 = 'count'
      const persons = list()

      persons.onSorterChange('rating_average', 'ascend')
      await flush()

      expect(store.sort).toBe('name')
      expect(store.sort2).toBe('rating_asc')
    })

    it('默认排序下第一次点表头 = 主排序；已有显式主排序则 = 次排序', async () => {
      const persons = list()
      persons.onSorterChange('rating_average', 'ascend')
      await flush()
      expect(store.sort).toBe('rating_asc')
      expect(store.sort2).toBe('')

      store.sort = 'newest'
      store.sort2 = ''
      persons.onSorterChange('inspiration_count', 'descend')
      await flush()
      expect(store.sort2).toBe('count')
      expect(store.order2).toBe('desc')
    })

    it('非可排序列与 onSortPicked', async () => {
      const persons = list()
      persons.onSorterChange('name', 'ascend')
      await flush()
      expect(store.load).not.toHaveBeenCalled()

      store.order = 'asc'
      store.sort2 = 'rating'
      persons.onSortPicked()
      expect(store.order).toBe('desc')
      expect(store.sort2).toBe('')
      expect(store.reload).toHaveBeenCalled()
    })

    it('clearSort 回到默认并重新加载；loadAndSync 页码越界时回退', async () => {
      store.sort = 'rating'
      store.page = 5
      store.total = 25
      store.size = 20
      const persons = list()

      persons.clearSort()
      await flush()
      expect(store.sort).toBe('count')
      expect(store.page).toBe(2) // maxPage = ceil(25/20)=2
      expect(store.load).toHaveBeenCalledTimes(2) // 越界后二次加载
      expect(mocks.replace).toHaveBeenCalled()
    })
  })

  describe('表格列 render', () => {
    it('人物列：有头像时传 imageUrl；自动登记来源显示提示标签', () => {
      const persons = list('blogger')
      const columns = persons.columns.value

      const withAvatar = renderCell(
        columnOf(columns, 'name'),
        person({ avatar_path: 'avatars/a.jpg' }),
      )
      const avatar = withAvatar.findComponent({ name: 'Avatar' })
      expect(avatar.props('imageUrl')).toBe('/files/avatars/a.jpg')
      expect(withAvatar.text()).toContain('博主甲')

      const auto = renderCell(columnOf(columns, 'name'), person({ source: 'auto_collect' }))
      expect(auto.text()).toContain('尚未进行自动登记')

      const plain = renderCell(columnOf(columns, 'name'), person())
      expect(plain.findComponent({ name: 'Avatar' }).props('imageUrl')).toBeUndefined()
      expect(plain.find('svg').exists()).toBe(true)
    })

    it('平台列：多平台徽标逐个展示；未知平台回退原值', () => {
      const persons = list('blogger')
      const column = columnOf(persons.columns.value, 'platform')

      const multi = renderCell(column, person({ group_platforms: ['xiaohongshu', 'douyin'] }))
      expect(multi.text()).toContain('小红书')
      expect(multi.text()).toContain('抖音')

      const unknown = renderCell(column, person({ platform: 'weibo' }))
      expect(unknown.text()).toBe('weibo')
    })

    it('人脸特征 / IP属地 / 素材数列的兜底', () => {
      const persons = list('blogger')
      const columns = persons.columns.value

      expect(
        renderCell(columnOf(columns, 'face_registered'), person({ face_registered: true })).text(),
      ).toContain('是')
      expect(
        renderCell(columnOf(columns, 'face_registered'), person({ face_registered: false })).text(),
      ).toContain('否')
      expect(
        renderCell(columnOf(columns, 'ip_location'), person({ ip_location: null })).text(),
      ).toBe('-')
      expect(
        renderCell(
          columnOf(columns, 'inspiration_count'),
          person({ inspiration_count: null }),
        ).text(),
      ).toBe('0')
    })

    it('作品评分列：样本足够显示分数与样本数，样本不足提示，未评分显示破折号', () => {
      const persons = list('blogger')
      const column = columnOf(persons.columns.value, 'rating_average')

      expect(renderCell(column, person({ rated_count: 6, rating_average: 4.25 })).text()).toContain(
        '★ 4.3 (6)',
      )
      expect(renderCell(column, person({ rated_count: 2 })).text()).toContain('样本不足 2/5')
      expect(renderCell(column, person({ rated_count: 0 })).text()).toContain('—')
    })

    it('来源列仅模特页展示，映射中文/未知值原样/缺省手动', () => {
      const blogger = list('blogger')
      expect(blogger.columns.value.some((c) => c.dataIndex === 'source')).toBe(false)

      const model = list('model')
      const column = columnOf(model.columns.value, 'source')
      expect(renderCell(column, person({ source: 'auto_collect' })).text()).toContain(
        '尚未进行自动登记',
      )
      expect(renderCell(column, person({ source: 'other' })).text()).toBe('other')
      expect(renderCell(column, person({ source: null })).text()).toBe('手动')
    })

    it('时间列走统一 renderTimeCell（保证单行）', () => {
      const persons = list('blogger')
      const columns = persons.columns.value

      expect(renderCell(columnOf(columns, 'last_inspiration_at'), person()).text()).toBe(
        'cell:date:2026-11-20T10:00:00Z',
      )
      expect(renderCell(columnOf(columns, 'created_at'), person()).text()).toBe(
        'cell:date:2026-11-01T10:00:00Z',
      )
    })

    it('操作列：详情/编辑按钮与删除确认；自动登记博主多一个「纳入追踪」', async () => {
      const persons = list('blogger')
      const record = person({ source: 'auto_collect' })
      const cell = renderCell(columnOf(persons.columns.value, 'actions'), record)

      const buttons = cell.findAllComponents(Button)
      const texts = buttons.map((b) => b.text())
      expect(texts).toContain('详情')
      expect(texts).toContain('编辑')
      expect(texts).toContain('纳入追踪')
      expect(texts).toContain('删除')

      const detailBtn = buttons.find((b) => b.text() === '详情')!
      await detailBtn.trigger('click')
      expect(mocks.push).toHaveBeenCalledWith({
        path: '/persons/1',
        query: expect.any(Object),
      })

      const editBtn = buttons.find((b) => b.text() === '编辑')!
      await editBtn.trigger('click')
      expect(persons.showForm.value).toBe(true)
      expect(persons.editingPerson.value?.id).toBe(1)

      const popconfirms = cell.findAllComponents(Popconfirm)
      expect(popconfirms).toHaveLength(2) // 纳入追踪 + 删除
      // h() 里的 onOk 会归一化成 「ok」事件监听；触发确认即执行对应处理
      popconfirms[0].vm.$emit('ok')
      await flush()
      expect(mocks.bloggerPromote).toHaveBeenCalled()
      popconfirms[1].vm.$emit('ok')
      await flush()
      expect(mocks.bloggerRemove).toHaveBeenCalled()
      await nextTick()
    })

    it('操作列：模特页没有「纳入追踪」', () => {
      const persons = list('model')
      const cell = renderCell(
        columnOf(persons.columns.value, 'actions'),
        person({ source: 'auto_collect' }),
      )

      const texts = cell.findAllComponents(Button).map((b) => b.text())
      expect(texts).not.toContain('纳入追踪')
      expect(texts).toContain('删除')
    })
  })
})
