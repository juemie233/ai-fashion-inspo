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

/** 来源中文映射 */
export const SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  ai_generated: 'AI 生成',
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

  // ── 列表状态 URL 持久化：页码/搜索/平台/排序写入 query，刷新与详情往返后原样恢复 ──

  /** 把列表上下文（kind + 页码 + 搜索 + 平台 + 排序）同步到 URL（replace 不堆历史） */
  function syncUrl() {
    const query: Record<string, string> = { kind }
    if (store.page > 1) query.page = String(store.page)
    if (store.search.trim()) query.q = store.search.trim()
    if (store.platform) query.platform = store.platform
    if (store.sort !== 'count') query.sort = store.sort
    router.replace({ path: '/persons', query })
  }

  /** 列表上下文变化时同步 URL：翻页/搜索/平台/排序任意变更即反映到地址栏 */
  watch(
    () => [store.page, store.search, store.platform, store.sort] as const,
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
    store.sort = q.sort === 'name' || q.sort === 'count' ? q.sort : 'count'
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
  const sortOptions = [
    { label: '素材数最多', value: 'count' },
    { label: '最新创建', value: 'newest' },
    { label: '名称排序', value: 'name' },
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

  /** 跳转人物详情：携带列表上下文（kind/页码/搜索/平台/排序），详情页返回或刷新后可恢复 */
  function goDetail(person: Person) {
    const query: Record<string, string> = { kind }
    if (store.page > 1) query.page = String(store.page)
    if (store.search.trim()) query.q = store.search.trim()
    if (store.platform) query.platform = store.platform
    if (store.sort !== 'count') query.sort = store.sort
    router.push({ path: `/persons/${person.id}`, query })
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

  const columns: TableColumnData[] = [
    {
      title: '人物',
      dataIndex: 'name',
      minWidth: 110,
      render: ({ record }) => {
        const row = record as Person
        return h('div', { class: 'person-cell' }, [
          h(
            Avatar,
            { size: 40 },
            {
              default: () =>
                // 展示优先级：人脸小图（自动裁剪）→ 手动头像 → SVG 人形占位
                row.face_thumb_path || row.avatar_path
                  ? h('img', {
                      src: getFileUrl(row.face_thumb_path || (row.avatar_path as string)),
                      alt: row.name,
                    })
                  : // 无头像时用通用人形图标占位，避免名字首字与名称并排造成「杨杨晨晨」式重复
                    h(
                      'svg',
                      { viewBox: '0 0 24 24', class: 'avatar-icon', 'aria-hidden': 'true' },
                      [
                        h('path', {
                          d: 'M12 12c2.7 0 4.9-2.2 4.9-4.9S14.7 2.2 12 2.2 7.1 4.4 7.1 7.1 9.3 12 12 12zm0 2.4c-3.4 0-10.1 1.7-10.1 5v2.4h20.2v-2.4c0-3.3-6.7-5-10.1-5z',
                        }),
                      ],
                    ),
            },
          ),
          h('span', { class: 'person-name' }, row.name),
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
      width: 80,
      render: ({ record }) => h('span', String((record as Person).inspiration_count ?? 0)),
    },
    // 「来源」列仅职业模特展示（穿搭博主不显示该列，其余能力不受影响）
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
  ]

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

  return {
    store,
    kindLabel,
    bloggerStats,
    loadBloggerStats,
    platformCount,
    platformOptions,
    sortOptions,
    showForm,
    editingPerson,
    openCreate,
    openEdit,
    handleDelete,
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
  }
}
