/** 人物列表核心逻辑（穿搭博主/职业模特共用）：URL 持久化、筛选、表格列、新建/编辑/删除、统计与排行。 */

import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Avatar,
  Button,
  Message,
  Popconfirm,
  Tag,
  type TableColumnData,
} from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { formatDate, renderTimeCell } from '@/utils/format'
import { getFileUrl } from '@/api/inspirations'
import { bloggersApi, fetchMissingProfiles, modelsApi, type PersonStats } from '@/api/persons'
import { usePersonsStore, type PersonKind } from '@/stores/persons'
import type { Person } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'

/** 来源中文映射（auto_collect：「我的喜欢」入库时按来源作者自动登记的博主） */
export const SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  ai_generated: 'AI 生成',
  // 用户口径（2026-11-24）：来源标签不能只写「自动登记」，容易被读成
  // 「已经完成自动登记」；明确写成「尚未进行自动登记」。
  auto_collect: '尚未进行自动登记',
}

/** 表头可排序的列 → 排序分组（与后端 sort/sort2 取值对应） */
const SORT_COLUMN_GROUP: Record<string, 'count' | 'rating'> = {
  inspiration_count: 'count',
  rating_average: 'rating',
}

/** 排序字段中文名（状态条展示用） */
const SORT_LABELS: Record<string, string> = {
  newest: '最新创建',
  name: '名称',
  count: '素材数',
  rating: '作品评分',
  rating_asc: '作品评分',
}

/** 允许出现在次排序（sort2）里的字段 */
const SECONDARY_SORT_KEYS = ['name', 'count', 'rating', 'rating_asc'] as const

/** 排序状态文案：count 用 order 方向，rating 方向编码在 rating/rating_asc 里 */
export function formatSortLabel(sort: string, order: 'asc' | 'desc'): string {
  if (sort === 'rating') return '作品评分 ↓'
  if (sort === 'rating_asc') return '作品评分 ↑'
  if (sort === 'count') return `素材数 ${order === 'asc' ? '↑' : '↓'}`
  return SORT_LABELS[sort] ?? sort
}

/** 是否为评分排序：后端在 sort 或 sort2 命中评分时，样本不足者保留但排最后 */
export function isRatingSort(sort: string | undefined | null): boolean {
  return sort === 'rating' || sort === 'rating_asc'
}

/** 排序键所属分组（count / rating / 其它） */
function sortGroup(sort: string | undefined | null): 'count' | 'rating' | null {
  if (sort === 'count') return 'count'
  if (isRatingSort(sort)) return 'rating'
  return null
}

export function usePersonList(kind: PersonKind) {
  const router = useRouter()
  const route = useRoute()
  const store = usePersonsStore(kind)

  const kindLabel = computed(() => (kind === 'blogger' ? '穿搭博主' : '职业模特'))

  // ── 数量统计（总数 + 平台分布；仅穿搭博主页顶部展示）──
  const bloggerStats = ref<PersonStats | null>(null)

  async function loadBloggerStats() {
    if (kind !== 'blogger') return
    try {
      bloggerStats.value = await bloggersApi.fetchStats()
    } catch {
      // 统计加载失败静默（顶部卡片不显示，不影响列表功能）
    }
  }

  /** 指定平台的博主数量 */
  function platformCount(platform: string): number {
    return bloggerStats.value?.items.find((i) => i.platform === platform)?.count ?? 0
  }

  // 列表数据量变化（新建/删除）时刷新统计
  watch(
    () => store.total,
    () => {
      if (kind === 'blogger') void loadBloggerStats()
    },
  )

  // ── 列表状态 URL 持久化：页码/搜索/平台/两级排序写入 query，刷新与详情往返后原样恢复 ──

  /** 当前列表上下文 → URL query（默认值不写，保持地址栏干净） */
  function buildListQuery(): Record<string, string> {
    const query: Record<string, string> = { kind }
    if (store.page > 1) query.page = String(store.page)
    if (store.search.trim()) query.q = store.search.trim()
    if (store.platform) query.platform = store.platform
    if (store.source) query.source = store.source
    if (store.sort !== 'count') query.sort = store.sort
    if (store.sort === 'count' && store.order !== 'desc') query.order = store.order
    if (store.sort2) query.sort2 = store.sort2
    if (store.sort2 === 'count' && store.order2 !== 'desc') query.order2 = store.order2
    if (store.ratingMin !== '') query.rating_min = String(store.ratingMin)
    return query
  }

  /** 把列表上下文同步到 URL（replace 不堆历史） */
  function syncUrl() {
    router.replace({ path: '/persons', query: buildListQuery() })
  }

  /** 列表上下文变化时同步 URL：翻页/搜索/平台/来源/评分筛选/两级排序任意变更即反映到地址栏 */
  watch(
    () =>
      [
        store.page,
        store.search,
        store.platform,
        store.source,
        store.sort,
        store.order,
        store.sort2,
        store.order2,
        store.ratingMin,
      ] as const,
    () => syncUrl(),
  )

  /** 从 URL query 恢复列表上下文（组件挂载时调用，刷新/详情返回后保持原状态）；
   *  默认排序为「素材数最多」（count），URL 未显式指定时按此进入 */
  function restoreFromUrl() {
    const q = route.query
    const page = Number(q.page)
    store.page = Number.isInteger(page) && page > 1 ? page : 1
    store.search = typeof q.q === 'string' ? q.q : ''
    store.platform = typeof q.platform === 'string' ? q.platform : ''
    store.source = typeof q.source === 'string' ? q.source : ''
    const ratingMin = Number(q.rating_min)
    store.ratingMin =
      Number.isInteger(ratingMin) && ratingMin >= 1 && ratingMin <= 5 ? ratingMin : ''
    store.sort =
      q.sort === 'name' || q.sort === 'count' || q.sort === 'rating' || q.sort === 'rating_asc'
        ? q.sort
        : 'count'
    store.order = q.order === 'asc' ? 'asc' : 'desc'
    // 次排序：非法值当作「没有次排序」，避免刷新后带着不认识的后端参数
    store.sort2 =
      typeof q.sort2 === 'string' && (SECONDARY_SORT_KEYS as readonly string[]).includes(q.sort2)
        ? (q.sort2 as (typeof SECONDARY_SORT_KEYS)[number])
        : ''
    store.order2 = q.order2 === 'asc' ? 'asc' : 'desc'
  }

  /** 加载后修正页码越界（如删除后总页数减少），并同步 URL */
  async function loadAndSync(force: boolean = true) {
    await store.load(force)
    const maxPage = Math.max(1, Math.ceil(store.total / store.size))
    if (store.page > maxPage) {
      store.page = maxPage
      await store.load(true)
    }
    syncUrl()
  }

  // ── 筛选选项 ──

  const platformOptions = [
    { label: '全部平台', value: '' },
    ...Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value })),
  ]
  const sortOptions = computed(() => [
    { label: '素材数最多', value: 'count' },
    { label: '最新创建', value: 'newest' },
    { label: '名称排序', value: 'name' },
    ...(kind === 'blogger'
      ? [
          { label: '评分最高（至少5张已评）', value: 'rating' },
          { label: '评分最低（至少5张已评）', value: 'rating_asc' },
        ]
      : []),
  ])
  /** 作品均分筛选（仅博主；至少 5 张已评分才参与） */
  const ratingOptions = [
    { label: '全部评分', value: '' },
    { label: '≥ 3 星（至少5张已评）', value: 3 },
    { label: '≥ 4 星（至少5张已评）', value: 4 },
    { label: '5 星（至少5张已评）', value: 5 },
  ]

  // ── 两级排序状态（表头点击 + 状态条）──

  /** 主排序文案（如「素材数 ↓」） */
  const sortMainLabel = computed(() => formatSortLabel(store.sort, store.order))
  /** 次排序文案（无次排序时为空串） */
  const sortSecondaryLabel = computed(() =>
    store.sort2 ? formatSortLabel(store.sort2, store.order2) : '',
  )
  /** 评分排序是否激活（主或次）：样本不足排在最后但仍保留，页面提示口径 */
  const ratingSortActive = computed(() => isRatingSort(store.sort) || isRatingSort(store.sort2))
  const ratingSortHint =
    '评分排序会把样本不足（已评 <5 张）的博主排在最后；评分筛选仍只显示样本足够的博主'
  /** 是否偏离默认排序（默认素材数降序、无次排序）：控制「清除排序」是否出现 */
  const hasActiveSort = computed(
    () => store.sort !== 'count' || store.order !== 'desc' || Boolean(store.sort2),
  )

  /** 重置到默认排序（素材数降序、清空次排序） */
  function resetSortState() {
    store.sort = 'count'
    store.order = 'desc'
    store.sort2 = ''
    store.order2 = 'desc'
  }

  /** 清除排序：回到默认排序并重新加载（评分排序清除后恢复全量博主） */
  function clearSort() {
    resetSortState()
    void loadAndSync()
  }
  /** 来源筛选：自动登记的博主（「我的喜欢」顺手补建）动辄几百个，必须能一键分开看 */
  const sourceOptions = [
    { label: '全部来源', value: '' },
    ...Object.entries(SOURCE_LABELS).map(([value, label]) => ({ label, value })),
  ]

  // ── 新建 / 编辑 / 删除 ──

  const showForm = ref(false)
  const editingPerson = ref<Person | null>(null)

  function openCreate() {
    editingPerson.value = null
    showForm.value = true
  }

  function openEdit(person: Person) {
    editingPerson.value = person
    showForm.value = true
  }

  /** 删除人物 */
  async function handleDelete(person: Person) {
    try {
      const api = kind === 'blogger' ? bloggersApi : modelsApi
      await api.remove(person.id)
      Message.success(`已删除${kindLabel.value}「${person.name}」`)
      // 保持当前页：本页仅剩一条且不在第一页时回退一页，否则按当前页刷新
      if (store.persons.length === 1 && store.page > 1) {
        await store.setPage(store.page - 1)
      } else {
        await store.load(true)
      }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  /**
   * 「纳入追踪」：把「我的喜欢」自动登记的博主转为已登记博主。
   *
   * 自动登记的博主算素材归属、但不算已登记博主，所以「一键获取素材（博主主页）」
   * 不会去下载 TA 的主页作品（避免点赞几百个作者后被一次性全量翻页）。用户确认要
   * 追踪时点这个按钮，source 改回 manual 即进下载白名单。
   *
   * 注意：进白名单只是必要条件——f2 还得知道该账号的 sec_user_id（见 run_fetch 的
   * targets 取自 f2 自己的用户库），所以提示文案要把这个前提讲清楚。
   */
  async function handlePromote(person: Person) {
    try {
      await bloggersApi.promote(person.id)
      Message.success(
        `已把「${person.name}」纳入追踪：f2 用户库里已有该账号时，「一键获取素材」会增量下载 TA 的主页作品`,
      )
      await store.load(true)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '纳入追踪失败'))
    }
  }

  /** 跳转人物详情：携带列表上下文（kind/页码/搜索/平台/两级排序），详情页返回或刷新后可恢复 */
  function goDetail(person: Person) {
    router.push({ path: `/persons/${person.id}`, query: buildListQuery() })
  }

  // ── 人物组展开（方案 B）：同组折叠为一条主记录，展开行显示组内账号 ──
  // 注意：Arco Table 的 rowKey 是「字段名」（String 类型，内部用 record[rowKey] 取值），
  // 表格用 row-key="id"，因此 record.key 是数字 id；expandedRowKeys 必须存数字，
  // 否则 includes 严格相等比较永远不命中，展开不生效

  const expandedGroupIds = ref<number[]>([])

  /** 同步展开行 keys（Arco onExpandedChange 返回全部展开行，天然覆盖展开与折叠） */
  function setExpandedGroupIds(keys: number[]) {
    expandedGroupIds.value = keys
  }

  /** 搜索输入：回车触发（兼容中文输入法，compositionend 期间不误触） */
  function onSearchKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.isComposing) {
      store.reload()
    }
  }

  // ── 表格列定义 ──

  const columns = computed<TableColumnData[]>(() => [
    {
      title: '人物',
      dataIndex: 'name',
      // 头像 + 网名必须单行；自动登记标签放到网名下方，避免挤占同一行宽度
      width: 210,
      minWidth: 190,
      render: ({ record }) => {
        const row = record as Person
        return h('div', { class: 'person-cell' }, [
          h(
            Avatar,
            {
              size: 40,
              // 头像用 imageUrl 传入：Arco 只在 imageUrl 分支给 wrapper 加
              // arco-avatar-image，圆形裁剪（overflow + border-radius）才生效；
              // 插槽里的 img 不会被裁，会按原图尺寸渲染成方形并溢出容器
              imageUrl: row.avatar_path ? getFileUrl(row.avatar_path) : undefined,
            },
            {
              // 没设置过头像时用通用人形图标占位（避免名字首字与名称并排造成
              // 「杨杨晨晨」式重复）；有 imageUrl 时 Arco 忽略这个插槽
              default: () =>
                h('svg', { viewBox: '0 0 24 24', class: 'avatar-icon', 'aria-hidden': 'true' }, [
                  h('path', {
                    d: 'M12 12c2.7 0 4.9-2.2 4.9-4.9S14.7 2.2 12 2.2 7.1 4.4 7.1 7.1 9.3 12 12 12zm0 2.4c-3.4 0-10.1 1.7-10.1 5v2.4h20.2v-2.4c0-3.3-6.7-5-10.1-5z',
                  }),
                ]),
            },
          ),
          h('div', { class: 'person-meta' }, [
            h('span', { class: 'person-name', title: row.name }, row.name),
            // 「我的喜欢」入库时自动登记的博主：算素材归属但不算已登记博主（不进下载
            // 白名单），列表里明确标出来，用户点「纳入追踪」后才开始抓主页作品
            row.source === 'auto_collect'
              ? h(
                  Tag,
                  {
                    size: 'small',
                    color: 'orange',
                    class: 'person-source-tag',
                    title:
                      '由「我的喜欢」采集的来源作者；尚未进行自动登记，点「纳入追踪」后才会进「一键获取素材」的下载白名单',
                  },
                  { default: () => SOURCE_LABELS.auto_collect },
                )
              : null,
          ]),
        ])
      },
    },
    {
      title: '平台',
      dataIndex: 'platform',
      width: 90,
      render: ({ record }) => {
        const row = record as Person
        // 人物组（方案 B）：组内多平台时显示多平台徽标（如 小红书+抖音）
        if (row.group_platforms?.length) {
          return h('span', { style: 'white-space: nowrap' }, [
            row.group_platforms.map((p, i) =>
              h(
                'span',
                {
                  key: p + i,
                  style:
                    'display:inline-block;margin-right:4px;padding:1px 6px;border-radius:4px;' +
                    'background:#eef4fd;color:#2a78d6;font-size:12px;',
                },
                PERSON_PLATFORM_LABELS[p] || p,
              ),
            ),
          ])
        }
        return PERSON_PLATFORM_LABELS[row.platform] || row.platform
      },
    },
    {
      title: '人脸特征',
      dataIndex: 'face_registered',
      width: 88,
      render: ({ record }) => {
        const row = record as Person
        return row.face_registered
          ? h(Tag, { color: 'green', size: 'small' }, { default: () => '是' })
          : h(Tag, { color: 'gray', size: 'small' }, { default: () => '否' })
      },
    },
    {
      title: 'IP属地',
      dataIndex: 'ip_location',
      width: 90,
      render: ({ record }) => (record as Person).ip_location || '-',
    },
    {
      title: '素材数',
      dataIndex: 'inspiration_count',
      width: 96,
      // 表头可点击排序：升序找「素材最少」（常用于发现空人物），降序找高频人物。
      // sortOrder 是受控回显（刷新/往返详情后箭头仍指向当前方向）；点击由 onSorterChange 接管。
      sortable: {
        sortDirections: ['descend', 'ascend'],
        // Arco 只在 sortable.sortOrder 里受控回显；放在列顶层不会生效
        sortOrder: store.sort === 'count' ? (store.order === 'asc' ? 'ascend' : 'descend') : '',
      },
      render: ({ record }) => h('span', String((record as Person).inspiration_count ?? 0)),
    },
    // 「来源」列仅职业模特展示（穿搭博主不显示该列，其余能力不受影响）
    ...(kind === 'blogger'
      ? ([
          {
            title: '作品评分',
            dataIndex: 'rating_average',
            width: 120,
            // 表头可点击排序：降序=高分博主在前，升序=低分（但样本足够）在前；
            // sort=rating / rating_asc 由后端统一把「不足 5 张已评」的排到最后。
            sortable: {
              sortDirections: ['descend', 'ascend'],
              sortOrder:
                store.sort === 'rating' ? 'descend' : store.sort === 'rating_asc' ? 'ascend' : '',
            },
            render: ({ record }) => {
              const row = record as Person
              const rated = row.rated_count ?? 0
              if (rated >= 5 && row.rating_average != null) {
                return h(
                  'span',
                  { style: 'white-space: nowrap' },
                  `★ ${row.rating_average.toFixed(1)} (${rated})`,
                )
              }
              if (rated > 0) {
                return h(
                  'span',
                  { style: 'color: #999; white-space: nowrap' },
                  `样本不足 ${rated}/5`,
                )
              }
              return h('span', { style: 'color: #999' }, '—')
            },
          },
        ] as TableColumnData[])
      : []),
    ...(kind === 'model'
      ? ([
          {
            title: '来源',
            dataIndex: 'source',
            width: 90,
            render: ({ record }) => {
              const row = record as Person
              return SOURCE_LABELS[row.source || 'manual'] || row.source
            },
          },
        ] as TableColumnData[])
      : []),
    {
      title: '最近素材',
      dataIndex: 'last_inspiration_at',
      width: 160,
      // 时间列一律单行（规则 7）：判断博主是否还在更新，不用点进详情
      render: ({ record }) => renderTimeCell(formatDate((record as Person).last_inspiration_at)),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: ({ record }) => renderTimeCell(formatDate((record as Person).created_at)),
    },
    {
      title: '操作',
      dataIndex: 'actions',
      width: 230,
      // 固定在表格右侧：列宽合计超过容器宽度出现横向滚动时，操作按钮始终可见
      fixed: 'right',
      // 注：render 中必须使用导入的组件对象（Button/Popconfirm），
      // 字符串组件名在 render 场景可能解析失败导致按钮不渲染
      render: ({ record }) => {
        const row = record as Person
        return h('div', { class: 'row-actions' }, [
          h(
            Button,
            { size: 'small', type: 'text', onClick: () => goDetail(row) },
            { default: () => '详情' },
          ),
          h(
            Button,
            { size: 'small', type: 'secondary', onClick: () => openEdit(row) },
            { default: () => '编辑' },
          ),
          // 自动登记的博主（我的喜欢来源作者）：一键纳入追踪白名单
          kind === 'blogger' && row.source === 'auto_collect'
            ? h(
                Popconfirm,
                {
                  content: `把「${row.name}」纳入追踪？TA 会进入「一键获取素材」的下载名单（前提：f2 用户库里已有该账号，即曾用 f2 抓过 TA 的主页）。`,
                  onOk: () => handlePromote(row),
                },
                {
                  default: () =>
                    h(
                      Button,
                      { size: 'small', type: 'outline', status: 'warning' },
                      { default: () => '纳入追踪' },
                    ),
                },
              )
            : null,
          h(
            Popconfirm,
            {
              content: `确定删除${kindLabel.value}「${row.name}」？仅当该人物无关联素材时才可删除。`,
              onOk: () => handleDelete(row),
            },
            {
              default: () =>
                h(Button, { size: 'small', status: 'danger' }, { default: () => '删除' }),
            },
          ),
        ])
      },
    },
  ])

  // ── 热门排行（侧栏展示）──

  const topPersons = ref<Person[]>([])

  async function loadTop() {
    try {
      const api = kind === 'blogger' ? bloggersApi : modelsApi
      topPersons.value = (await api.fetchTop(5)) ?? []
    } catch {
      // 排行加载失败不阻塞主列表
    }
  }

  // ── 博主主页信息补全（仅穿搭博主）：缺失计数徽标（弹窗与任务逻辑在 useBloggerEnrich）──

  const missingTotal = ref(0)

  async function loadMissingCount() {
    if (kind !== 'blogger') return
    try {
      const data = await fetchMissingProfiles()
      missingTotal.value = data.total
    } catch {
      // 数量加载失败静默（按钮无徽标，不影响其它功能）
    }
  }

  onMounted(async () => {
    // 从 URL 恢复列表上下文（刷新 / 从详情返回时保持原页码与筛选），再加载数据
    restoreFromUrl()
    await loadAndSync(true)
    await loadTop()
    await loadMissingCount()
    await loadBloggerStats()
  })

  /** 下拉切换排序方式：素材数排序一律回到「降序」（与「素材数最多」文案一致）。
   *  同时清空次排序——下拉是单值控件，不能留下看不见的第二级排序条件。 */
  function onSortPicked() {
    store.order = 'desc'
    store.sort2 = ''
    store.order2 = 'desc'
    store.reload()
  }

  /** 应用主排序 */
  function applyPrimarySort(group: 'count' | 'rating', ascending: boolean) {
    if (group === 'count') {
      store.sort = 'count'
      store.order = ascending ? 'asc' : 'desc'
    } else {
      // 评分的升降序编码在 sort 取值里（Rating 后端契约：rating / rating_asc）
      store.sort = ascending ? 'rating_asc' : 'rating'
      store.order = 'desc'
    }
  }

  /** 应用次排序 */
  function applySecondarySort(group: 'count' | 'rating', ascending: boolean) {
    if (group === 'count') {
      store.sort2 = 'count'
      store.order2 = ascending ? 'asc' : 'desc'
    } else {
      store.sort2 = ascending ? 'rating_asc' : 'rating'
      store.order2 = 'desc'
    }
  }

  /** 主排序列被表头清除时：次排序提升为主排序（保持用户当前选择不丢）；没有次排序则回默认 */
  function promoteSecondaryToPrimary() {
    const secondary = store.sort2
    if (!secondary) {
      resetSortState()
      return
    }
    store.sort = secondary
    store.order = secondary === 'count' ? store.order2 : 'desc'
    store.sort2 = ''
    store.order2 = 'desc'
  }

  /**
   * 表头点击排序：两级规则（与用户确认的交互一致）。
   *
   * - 点主排序列：切换该列方向（direction 由 Arco 按当前箭头推出）；
   * - 点次排序列：切次排序方向（次列不显示表头箭头，方向按本地状态翻转）；
   * - 点新列且两列已满：替换次排序；
   * - 还没有显式主排序（默认素材数降序）：第一次点列 = 主排序；
   * - 已有主排序、次排序为空：第二次点其他列 = 次排序；
   * - Arco 第三次点击（direction=null）：清除该列（主列清除时次列提升为主）。
   */
  function onSorterChange(dataIndex: string, direction: string | null) {
    const group = SORT_COLUMN_GROUP[dataIndex]
    if (!group) return
    const primaryGroup = sortGroup(store.sort)
    const secondaryGroup = sortGroup(store.sort2)
    const isAscending = direction === 'ascend'

    if (direction !== 'ascend' && direction !== 'descend') {
      if (primaryGroup === group) {
        promoteSecondaryToPrimary()
      } else if (secondaryGroup === group) {
        store.sort2 = ''
        store.order2 = 'desc'
      }
      void loadAndSync()
      return
    }

    if (primaryGroup === group) {
      applyPrimarySort(group, isAscending)
    } else if (secondaryGroup === group) {
      if (group === 'count') {
        store.order2 = store.order2 === 'asc' ? 'desc' : 'asc'
      } else {
        store.sort2 = store.sort2 === 'rating' ? 'rating_asc' : 'rating'
      }
    } else if (store.sort2) {
      // 两列已满：新列替换次排序
      applySecondarySort(group, isAscending)
    } else if (!hasActiveSort.value) {
      // 仍是默认排序（用户还没点过表头）：第一次点列 = 主排序
      applyPrimarySort(group, isAscending)
    } else {
      // 已有显式主排序、次排序为空：第二次点其他列 = 次排序
      applySecondarySort(group, isAscending)
    }
    void loadAndSync()
  }

  return {
    store,
    kindLabel,
    bloggerStats,
    loadBloggerStats,
    platformCount,
    platformOptions,
    sortOptions,
    ratingOptions,
    sourceOptions,
    showForm,
    editingPerson,
    openCreate,
    openEdit,
    handleDelete,
    handlePromote,
    goDetail,
    onSearchKeydown,
    columns,
    topPersons,
    loadTop,
    missingTotal,
    loadMissingCount,
    loadAndSync,
    restoreFromUrl,
    expandedGroupIds,
    setExpandedGroupIds,
    onSorterChange,
    onSortPicked,
    sortMainLabel,
    sortSecondaryLabel,
    ratingSortActive,
    ratingSortHint,
    hasActiveSort,
    clearSort,
  }
}
