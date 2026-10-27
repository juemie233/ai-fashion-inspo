/** 人物详情页的「增强区」状态：洞察、风格相近、人脸注册候选、资料补齐。
 *
 * 为什么单开一个 composable：这些数据都属于「详情页附属信息」——进页面时并行拉取、
 * 互不阻塞主详情；失败也只是少一块区域，不该让人物详情整体报错。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  bloggersApi,
  type PersonFaceCandidates,
  type PersonFaceCluster,
  type PersonInsights,
  type PersonSimilarItem,
} from '@/api/persons'
import type { PersonType } from '@shared/types/person'

export function usePersonDetailExtras(
  personId: () => number,
  kind: () => PersonType,
  onProfileUpdated: () => Promise<void> | void,
) {
  const insights = ref<PersonInsights | null>(null)
  const insightsLoading = ref(false)
  const similar = ref<PersonSimilarItem[]>([])
  /** 疑似重复（归一化同名）：后端返回 {key, items[]}，只保留包含当前人物的分组 */
  const duplicates = ref<
    Array<{
      key: string
      items: Array<{ id: number; name: string; inspiration_count?: number }>
    }>
  >([])
  const similarLoading = ref(false)
  const enriching = ref(false)

  /** 人脸候选（弹窗打开时才拉，避免每次进详情页都做一次聚类） */
  const faceCandidates = ref<PersonFaceCandidates | null>(null)
  const faceCandidatesLoading = ref(false)
  const faceCandidatesVisible = ref(false)
  const faceRegistering = ref(false)

  /** 洞察与相似度是「锦上添花」：失败静默降级，不打断详情页 */
  async function loadInsightsAndSimilar(): Promise<void> {
    insightsLoading.value = true
    similarLoading.value = true
    try {
      insights.value = await bloggersApi.fetchInsights(personId())
    } catch {
      insights.value = null
    } finally {
      insightsLoading.value = false
    }
    try {
      const data = await bloggersApi.fetchSimilar(personId())
      similar.value = data.similar ?? []
      duplicates.value = (data.duplicates ?? []) as typeof duplicates.value
    } catch {
      similar.value = []
      duplicates.value = []
    } finally {
      similarLoading.value = false
    }
  }

  /** 补齐资料卡（离线读 f2 用户库）：成功刷新详情，失败把后端原因展示出来 */
  async function enrichProfile(): Promise<boolean> {
    if (kind() !== 'blogger') return false
    enriching.value = true
    try {
      const result = await bloggersApi.enrichProfile(personId())
      if (result.status === 'updated') {
        const fields = (result.updated_fields ?? []).join('、')
        Message.success(fields ? `资料已补齐：${fields}` : '资料已补齐')
        await onProfileUpdated()
        return true
      }
      Message.info(result.reason || '没有可补的资料（f2 用户库里没有这个账号的资料）')
      return false
    } catch (e) {
      Message.error(getApiErrorMessage(e, '补齐资料失败'))
      return false
    } finally {
      enriching.value = false
    }
  }

  /** 打开人脸候选弹窗（首次打开才拉数据；已取消可再拉一次） */
  async function openFaceCandidates(): Promise<void> {
    faceCandidatesVisible.value = true
    if (faceCandidates.value) return
    faceCandidatesLoading.value = true
    try {
      faceCandidates.value = await bloggersApi.fetchFaceCandidates(personId())
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载人脸候选失败'))
      faceCandidates.value = null
    } finally {
      faceCandidatesLoading.value = false
    }
  }

  /**
   * 用候选素材注册人脸（A3a/A3b 共用）：`registerFace` 传 inspiration_ids，
   * 后端取这些素材里的主脸做平均池化，覆盖旧特征。
   */
  async function registerFaceFromIds(ids: string[]): Promise<boolean> {
    if (!ids.length) {
      Message.warning('请先勾选要用来注册的素材')
      return false
    }
    if (ids.length > 5) {
      Message.warning('最多只能用 5 张素材注册（合计），请减少勾选')
      return false
    }
    faceRegistering.value = true
    try {
      await bloggersApi.registerFace(personId(), [], ids)
      Message.success('人脸已注册（用选中的素材特征）')
      faceCandidatesVisible.value = false
      faceCandidates.value = null
      await onProfileUpdated()
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '注册人脸失败'))
      return false
    } finally {
      faceRegistering.value = false
    }
  }

  /** 单个候选簇 → 该簇里置信度最高的 3 张（后端已排好序） */
  function idsOfCluster(cluster: PersonFaceCluster): string[] {
    return cluster.inspiration_ids.slice(0, 5)
  }

  const hasFaceCandidates = computed(() => (faceCandidates.value?.clusters.length ?? 0) > 0)

  return {
    insights,
    insightsLoading,
    similar,
    duplicates,
    similarLoading,
    enriching,
    faceCandidates,
    faceCandidatesLoading,
    faceCandidatesVisible,
    faceRegistering,
    hasFaceCandidates,
    loadInsightsAndSimilar,
    enrichProfile,
    openFaceCandidates,
    registerFaceFromIds,
    idsOfCluster,
  }
}
