/**
 * 四个此前没有单测视图的挂载冒烟（2026-11-04 测试体系评测 P1-3）。
 *
 * 只验证「视图能挂载、标题/核心容器渲染、不抛异常」；业务交互由各自
 * composable / 子组件用例覆盖。SearchView 是核心搜索入口，优先保证不白屏。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { flushPromises, shallowMount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  routerReplace: vi.fn(),
  routerPush: vi.fn(),
  routeQuery: {} as Record<string, string>,
  apiGet: vi.fn(),
  batchTrash: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
  useRouter: () => ({ replace: mocks.routerReplace, push: mocks.routerPush }),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.apiGet, post: vi.fn() },
}))
vi.mock('@/api/inspirations', () => ({ batchTrash: mocks.batchTrash }))

vi.mock('@/composables/useSearch', () => {
  const r = (v: unknown) => ref(v)
  return {
    useSearch: () => ({
      searchBarRef: r(null),
      tagsStore: {
        selectedTags: new Set<string>(),
        excludedTags: new Set<string>(),
        combineMode: 'and',
      },
      results: r([]),
      total: r(0),
      searching: r(false),
      filterVisible: r(false),
      keyword: r(''),
      currentPage: r(1),
      pageSize: r(20),
      sortMode: r('latest'),
      sourceFilter: r(''),
      mediaFilter: r(''),
      analysisFilter: r(''),
      dateFrom: r(''),
      dateTo: r(''),
      ratingMin: r(''),
      density: r('comfortable'),
      vectorMode: r('none'),
      semanticText: r(''),
      vectorLoading: r(false),
      vectorQueryLabel: r(''),
      imagePreviewUrl: r(''),
      searchHistory: r([]),
      vectorBadges: r([]),
      totalPages: r(0),
      sortOptions: [],
      copySearchLink: vi.fn(),
      doSearch: vi.fn(),
      handleSearchBar: vi.fn(),
      doSemanticSearch: vi.fn(),
      handleImagePicked: vi.fn(),
      exitVectorMode: vi.fn(),
      handleDelete: vi.fn(),
      handleToggleFavorite: vi.fn(),
      handleRate: vi.fn(),
      applyHistory: vi.fn(),
      clearHistory: vi.fn(),
    }),
  }
})

vi.mock('@/composables/usePageTitle', () => ({ usePageTitle: vi.fn() }))
vi.mock('@/composables/usePersonDetail', () => {
  const r = (v: unknown) => ref(v)
  return {
    usePersonDetail: () => ({
      personId: r(1),
      api: r(null),
      kind: r('blogger'),
      kindLabel: r('博主'),
      detail: r(null),
      loading: r(true),
      isProfileUrlSafe: r(true),
      items: r([]),
      total: r(0),
      page: r(1),
      pageSize: r(20),
      itemsLoading: r(false),
      lightboxOpen: r(false),
      lightboxPaths: r([]),
      photoSets: r([]),
      photoSetsLoading: r(false),
      photoLightboxOpen: r(false),
      photoLightboxPaths: r([]),
      showForm: r(false),
      backToList: vi.fn(),
      handleDelete: vi.fn(),
      goSearchByTag: vi.fn(),
      openPhotoSet: vi.fn(),
      handleDeletePhotoSet: vi.fn(),
      goAddPhotos: vi.fn(),
      setPage: vi.fn(),
      loadDetail: vi.fn(),
      viewMode: r('grid'),
      sort: r('latest'),
      filters: r({}),
      works: r([]),
      worksTotal: r(0),
      worksTotalItems: r(0),
      worksPage: r(1),
      worksSize: r(20),
      worksLoading: r(false),
      setViewMode: vi.fn(),
      setWorksPage: vi.fn(),
      setFilters: vi.fn(),
      setSort: vi.fn(),
      unbindMode: r(false),
      unbindSelectedIds: r([]),
      unbindSubmitting: r(false),
      clearingAll: r(false),
      isCurrentPageAllSelected: r(false),
      enterUnbindMode: vi.fn(),
      exitUnbindMode: vi.fn(),
      toggleUnbindSelect: vi.fn(),
      toggleSelectCurrentPage: vi.fn(),
      confirmUnbindSelected: vi.fn(),
      unbindAll: vi.fn(),
    }),
  }
})
vi.mock('@/composables/usePersonDetailExtras', () => {
  const r = (v: unknown) => ref(v)
  return {
    usePersonDetailExtras: () => ({
      insights: r(null),
      insightsLoading: r(false),
      similar: r([]),
      duplicates: r([]),
      similarLoading: r(false),
      enriching: r(false),
      faceCandidates: r([]),
      faceCandidatesLoading: r(false),
      faceCandidatesVisible: r(false),
      faceRegistering: r(false),
      hasFaceCandidates: r(false),
      loadInsightsAndSimilar: vi.fn(),
      enrichProfile: vi.fn(),
      openFaceCandidates: vi.fn(),
      registerFaceFromIds: vi.fn(),
    }),
  }
})
vi.mock('@/composables/usePersonRandomBrowse', () => {
  const r = (v: unknown) => ref(v)
  return {
    usePersonRandomBrowse: () => ({
      visible: r(false),
      loading: r(false),
      item: r(null),
      total: r(0),
      historyCount: r(0),
      next: vi.fn(),
      prev: vi.fn(),
      open: vi.fn(),
      reset: vi.fn(),
    }),
  }
})
vi.mock('@/composables/useWorksDownload', () => ({
  useWorksDownload: () => ({
    loadStatus: vi.fn(),
    resolving: ref(false),
    state: ref({ disabled: true, reason: '', needsResolve: false }),
    downloadAllWorks: vi.fn(),
  }),
}))
vi.mock('@/api/persons', () => ({
  bloggersApi: { linkGroup: vi.fn() },
  modelsApi: {},
}))

import AdminView from '../AdminView.vue'
import InsightsView from '../InsightsView.vue'
import PersonDetailView from '../PersonDetailView.vue'
import SearchView from '../SearchView.vue'

function mountView(component: object) {
  return shallowMount(component as never, {
    global: { plugins: [ArcoVue] },
  })
}

describe('视图挂载冒烟', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiGet.mockResolvedValue({ data: { untagged_count: 1, analysis_failed_count: 0 } })
    mocks.batchTrash.mockResolvedValue({ trashed: 0, skipped: 0 })
  })

  it('SearchView 渲染高级搜索入口', () => {
    const wrapper = mountView(SearchView)
    expect(wrapper.text()).toContain('高级搜索')
  })

  it('AdminView 挂载并请求概览统计', async () => {
    const wrapper = mountView(AdminView)
    await flushPromises()
    expect(wrapper.text()).toContain('素材管理')
    expect(mocks.apiGet).toHaveBeenCalled()
  })

  it('InsightsView 渲染数据洞察', () => {
    const wrapper = mountView(InsightsView)
    expect(wrapper.text()).toContain('数据洞察')
  })

  it('PersonDetailView 加载态挂载不白屏', () => {
    const wrapper = mountView(PersonDetailView)
    expect(wrapper.find('.person-detail-page').exists()).toBe(true)
  })
})
