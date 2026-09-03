/** 人物详情页核心逻辑：路由参数解析、详情加载、素材列表与灯箱、照片组、编辑删除与导航。 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  bloggersApi,
  deleteModelPhotoSet,
  fetchModelPhotoSet,
  fetchModelPhotoSets,
  modelsApi,
  type ModelPhotoSet,
  type PersonInspiration,
} from '@/api/persons'
import type { InspirationOut } from '@/api/inspirations'
import type { PersonDetail, PersonType } from '@shared/types/person'

/** 人物详情页素材分页大小 */
const PAGE_SIZE = 30

export function usePersonDetail() {
  const route = useRoute()
  const router = useRouter()

  const personId = computed(() => Number(route.params.id))
  /** 人物种类：由列表页跳转时携带（/persons/:id?kind=blogger|model） */
  const kind = computed<PersonType>(() => (route.query.kind === 'model' ? 'model' : 'blogger'))
  /** 按种类选择 API（博主 / 模特已拆分） */
  const api = computed(() => (kind.value === 'model' ? modelsApi : bloggersApi))
  const kindLabel = computed(() => (kind.value === 'model' ? '职业模特' : '穿搭博主'))

  const detail = ref<PersonDetail | null>(null)
  const loading = ref(true)

  /** 主页链接是否安全可点击（仅允许 http/https，杜绝 javascript:/data: 注入） */
  const isProfileUrlSafe = computed(() => {
    const url = detail.value?.profile_url
    if (!url) return false
    try {
      const p = new URL(url)
      return p.protocol === 'http:' || p.protocol === 'https:'
    } catch {
      return false
    }
  })

  // ── 素材列表状态 ──
  const items = ref<PersonInspiration[]>([])
  const total = ref(0)
  const page = ref(1)
  const itemsLoading = ref(false)
  /** 灯箱是否打开（全屏浏览该人物全部图片素材） */
  const lightboxOpen = ref(false)

  /** 素材转 InspirationOut（复用 MasonryGrid） */
  function toInspirationOut(item: PersonInspiration): InspirationOut {
    return {
      id: item.inspiration_id,
      file_path: item.file_path,
      thumbnail_path: item.thumbnail_path,
      media_type: item.media_type,
      is_favorite: false,
      created_at: item.created_at || '',
      tags: [],
      analysis_status: 'none',
    }
  }

  /** 灯箱图片列表：该人物当前分页内的图片素材 */
  const lightboxPaths = computed<string[]>(() =>
    items.value.filter((i) => i.media_type !== 'video' && i.file_path).map((i) => i.file_path),
  )

  async function loadInspirations() {
    itemsLoading.value = true
    try {
      const data = await api.value.fetchInspirations(personId.value, page.value, PAGE_SIZE)
      items.value = data.items ?? []
      total.value = data.total ?? 0
    } catch {
      Message.error('加载人物素材失败')
    } finally {
      itemsLoading.value = false
    }
  }

  async function setPage(p: number) {
    page.value = p
    await loadInspirations()
  }

  /** 仅刷新详情（更新头部「N 条素材」统计），不碰 loading 遮罩 */
  async function refreshDetailStats() {
    try {
      detail.value = await api.value.fetchDetail(personId.value)
    } catch {
      // 统计刷新失败不阻塞
    }
  }

  // ── 人脸注销（多选）模式（仅博主）──
  /** 是否处于「人脸注销」多选模式：进入后网格可勾选，确定后批量注销选中素材的绑定 */
  const unbindMode = ref(false)
  /** 勾选待注销的素材 id 集合 */
  const unbindSelectedIds = ref<Set<string>>(new Set())
  /** 批量注销提交中 */
  const unbindSubmitting = ref(false)
  /** 清空全部绑定进行中 */
  const clearingAll = ref(false)

  /** 进入 / 退出人脸注销多选模式（退出时清空勾选） */
  function enterUnbindMode() {
    unbindMode.value = true
    unbindSelectedIds.value = new Set()
  }
  function exitUnbindMode() {
    unbindMode.value = false
    unbindSelectedIds.value = new Set()
  }
  /** 切换某个素材的勾选 */
  function toggleUnbindSelect(id: string) {
    const next = new Set(unbindSelectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    unbindSelectedIds.value = next
  }
  /** 当前页素材是否已全选 */
  const isCurrentPageAllSelected = computed(
    () =>
      items.value.length > 0 &&
      items.value.every((i) => unbindSelectedIds.value.has(i.inspiration_id)),
  )
  /** 全选 / 取消全选当前页（跨页勾选保留） */
  function toggleSelectCurrentPage() {
    const next = new Set(unbindSelectedIds.value)
    if (isCurrentPageAllSelected.value) {
      items.value.forEach((i) => next.delete(i.inspiration_id))
    } else {
      items.value.forEach((i) => next.add(i.inspiration_id))
    }
    unbindSelectedIds.value = next
  }

  /** 注销后统一刷新：当前页空了回退一页，退出模式，再拉素材 + 详情统计 */
  async function refreshAfterUnbind() {
    if (page.value > 1 && items.value.length <= unbindSelectedIds.value.size) {
      page.value -= 1
    }
    exitUnbindMode()
    await Promise.all([loadInspirations(), refreshDetailStats()])
  }

  /** 确定注销：把勾选素材上「识别为该博主的人脸匹配」与「归属绑定」一起注销（人脸特征保留） */
  async function confirmUnbindSelected() {
    if (kind.value !== 'blogger') return
    const ids = [...unbindSelectedIds.value]
    if (ids.length === 0) {
      Message.warning('请先勾选要注销的素材')
      return
    }
    unbindSubmitting.value = true
    try {
      const r = await bloggersApi.unbindInspirations(personId.value, ids)
      Message.success(
        `已注销 ${ids.length} 个素材的绑定：解除归属 ${r.inspirations_unlinked} 条、回退人脸记录 ${r.face_detections_cleared} 条`,
      )
      await refreshAfterUnbind()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '注销失败'))
    } finally {
      unbindSubmitting.value = false
    }
  }

  /** 清空该博主与全部素材的绑定（人脸特征保留） */
  async function unbindAll() {
    if (kind.value !== 'blogger') return
    clearingAll.value = true
    try {
      const r = await bloggersApi.unbindInspirations(personId.value)
      Message.success(
        `已清空全部素材绑定：解除 ${r.inspirations_unlinked} 条关联、回退人脸记录 ${r.face_detections_cleared} 条`,
      )
      exitUnbindMode()
      page.value = 1
      await Promise.all([loadInspirations(), refreshDetailStats()])
    } catch (e) {
      Message.error(getApiErrorMessage(e, '清空绑定失败'))
    } finally {
      clearingAll.value = false
    }
  }

  // ── 照片组（模特写真：与穿搭素材分离）──
  const photoSets = ref<ModelPhotoSet[]>([])
  const photoSetsLoading = ref(false)
  /** 照片组灯箱：浏览某个照片组的照片 */
  const photoLightboxOpen = ref(false)
  const photoLightboxPaths = ref<string[]>([])
  const photoLightboxName = ref('')

  async function loadPhotoSets() {
    photoSetsLoading.value = true
    try {
      const data = await fetchModelPhotoSets(personId.value, 1, 50)
      photoSets.value = data.items ?? []
    } catch {
      // 照片组加载失败不阻塞详情页其余内容
    } finally {
      photoSetsLoading.value = false
    }
  }

  /** 点击照片组：加载组内照片并打开灯箱浏览 */
  async function openPhotoSet(set: ModelPhotoSet) {
    try {
      const data = await fetchModelPhotoSet(personId.value, set.id, 1, 200)
      photoLightboxPaths.value = (data.photos ?? []).map((p) => p.file_path)
      photoLightboxName.value = set.name
      photoLightboxOpen.value = true
    } catch {
      Message.error('加载照片组失败')
    }
  }

  /** 删除照片组（二次确认） */
  async function handleDeletePhotoSet(set: ModelPhotoSet) {
    try {
      await deleteModelPhotoSet(personId.value, set.id)
      Message.success(`已删除照片组「${set.name}」`)
      await loadPhotoSets()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  /** 跳转到「添加模特照片」页并预选当前人物 */
  function goAddPhotos() {
    router.push({ path: '/model-photos', query: { person_id: personId.value } })
  }

  // ── 编辑 / 删除 ──
  const showForm = ref(false)

  /** 返回人物列表：携带进入详情页时的列表上下文（kind/页码/搜索/平台/排序），
   *  列表页据此恢复原分页与筛选，不再回到第一页 */
  function backToList() {
    const q = route.query
    const query: Record<string, string> = {}
    for (const key of ['kind', 'page', 'q', 'platform', 'sort'] as const) {
      const v = q[key]
      if (typeof v === 'string' && v) query[key] = v
    }
    router.push({ path: '/persons', query })
  }

  async function handleDelete() {
    if (!detail.value) return
    try {
      await api.value.remove(detail.value.id)
      Message.success(`已删除人物「${detail.value.name}」`)
      backToList()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除失败'))
    }
  }

  /** 点击风格标签跳转搜索页 */
  function goSearchByTag(name: string) {
    router.push({ path: '/search', query: { q: name } })
  }

  async function loadDetail() {
    // 参数兜底：非法 id（NaN/非正整数）直接回列表，避免 404 误报
    const id = personId.value
    if (!Number.isInteger(id) || id <= 0) {
      Message.error('人物参数无效')
      router.replace('/persons')
      return
    }
    loading.value = true
    try {
      detail.value = await api.value.fetchDetail(id)
    } catch {
      Message.error('加载人物详情失败')
      return
    } finally {
      loading.value = false
    }
    await loadInspirations()
    await loadPhotoSets()
    // 人脸注册状态与素材选择由对应子组件按 personId 变化自行加载/重置
  }

  onMounted(() => {
    loadDetail()
  })

  // 路由参数变化（未来人物间跳转 / 复用同一路由记录）时重新加载
  watch(personId, () => {
    page.value = 1
    loadDetail()
  })

  return {
    personId,
    kind,
    api,
    kindLabel,
    detail,
    loading,
    isProfileUrlSafe,
    items,
    total,
    page,
    pageSize: PAGE_SIZE,
    itemsLoading,
    lightboxOpen,
    toInspirationOut,
    lightboxPaths,
    photoSets,
    photoSetsLoading,
    photoLightboxOpen,
    photoLightboxPaths,
    photoLightboxName,
    loadPhotoSets,
    openPhotoSet,
    handleDeletePhotoSet,
    goAddPhotos,
    showForm,
    backToList,
    handleDelete,
    goSearchByTag,
    loadInspirations,
    setPage,
    loadDetail,
    unbindMode,
    unbindSelectedIds,
    unbindSubmitting,
    clearingAll,
    isCurrentPageAllSelected,
    enterUnbindMode,
    exitUnbindMode,
    toggleUnbindSelect,
    toggleSelectCurrentPage,
    confirmUnbindSelected,
    unbindAll,
  }
}
