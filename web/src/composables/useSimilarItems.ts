/** 相似素材推荐 + 相似素材批量添加大标签。 */

import { ref } from 'vue'
import type { Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  toggleFavorite,
  moveToTrash,
  batchAddTagsToInspirations,
  type InspirationDetailOut,
  type InspirationTagOut,
} from '@/api/inspirations'
import { fetchSimilar, type SimilarItemOut } from '@/api/search'

/** 相似来源中文标注映射 */
const SIMILAR_SOURCE_LABELS: Record<string, string> = {
  visual: '视觉相似',
  tag: '标签相似',
  hybrid: '视觉+标签',
}

/** 大标签下拉选项（与 useOutfitTags 中结构一致） */
export interface OutfitTagOption {
  label: string
  value: string
}

/**
 * 相似素材推荐 + 批量添加大标签的状态与逻辑。
 * @param detail           素材详情 ref，批量成功后用于刷新相似列表
 * @param outfitTagOptions 大标签下拉选项 ref（可被批量模式扩充）
 * @param outfitTags       获取当前素材穿搭大标签的函数
 * @param isCurrentSeq     判断某个详情加载序号是否仍为最新（防止过期响应覆盖新数据）
 */
export function useSimilarItems(
  detail: Ref<InspirationDetailOut | null>,
  outfitTagOptions: Ref<OutfitTagOption[]>,
  outfitTags: () => InspirationTagOut[],
  isCurrentSeq: (seq: number) => boolean,
) {
  
  const similarItems = ref<SimilarItemOut[]>([])
  const similarLoading = ref(false)
  const batchMode = ref(false)                 // 是否处于批量选择模式
  const batchSelectedIds = ref<string[]>([])   // 勾选的相似素材 ID
  const batchTagNames = ref<string[]>([])      // 要批量添加的大标签（预填当前素材大标签）
  const batchAdding = ref(false)

  /** 相似来源中文标注 */
  function similarSourceLabel(source: string): string {
    return SIMILAR_SOURCE_LABELS[source] || source
  }

  /** 加载相似素材推荐（视觉 + 标签加权） */
  async function loadSimilar(id: string, seq: number) {
    similarLoading.value = true
    try {
      const data = await fetchSimilar(id, 10)
      if (!isCurrentSeq(seq)) return  // 过期响应不覆盖新数据
      similarItems.value = data.similar
    } catch {
      // 相似推荐失败不影响详情展示，静默降级
      if (!isCurrentSeq(seq)) return
      similarItems.value = []
    } finally {
      if (isCurrentSeq(seq)) similarLoading.value = false
    }
  }

  /** 刷新相似素材（批量加标签后卡片标签与相似度可能变化） */
  async function refreshSimilar(id: string) {
    try {
      const data = await fetchSimilar(id, 10)
      // 竞态防护：批量打标在途时切换了素材，丢弃旧素材的相似列表
      if (detail.value?.id !== id) return
      similarItems.value = data.similar
    } catch {
      /* 静默降级 */
    }
  }

  /** 切换相似素材收藏 */
  async function toggleFavoriteSimilar(id: string) {
    const item = similarItems.value.find((s) => s.inspiration.id === id)?.inspiration
    if (!item) return
    try {
      const newState = !item.is_favorite
      await toggleFavorite(id, newState)
      item.is_favorite = newState
    } catch {
      Message.error('操作失败')
    }
  }

  /** 将相似素材移入垃圾桶 */
  async function deleteSimilar(id: string) {
    try {
      await moveToTrash(id)
      similarItems.value = similarItems.value.filter((s) => s.inspiration.id !== id)
      Message.success('已移入垃圾桶')
    } catch {
      Message.error('操作失败')
    }
  }

  /** 进入批量模式：预填当前素材已有的大标签，清空勾选 */
  function enterBatchMode() {
    batchSelectedIds.value = []
    const current = outfitTags().map((t) => t.tag.name)
    // 确保当前素材大标签在可选项中（AI 建议入库的标签可能尚未进 options）
    for (const name of current) {
      if (!outfitTagOptions.value.some((o) => o.value === name)) {
        outfitTagOptions.value.push({ label: name, value: name })
      }
    }
    batchTagNames.value = current
    batchMode.value = true
  }

  /** 退出批量模式 */
  function exitBatchMode() {
    batchMode.value = false
    batchSelectedIds.value = []
    batchTagNames.value = []
  }

  /** 切换单个相似素材的勾选 */
  function toggleSelectSimilar(id: string) {
    const idx = batchSelectedIds.value.indexOf(id)
    if (idx >= 0) batchSelectedIds.value.splice(idx, 1)
    else batchSelectedIds.value.push(id)
  }

  /** 全选 / 取消全选 */
  function toggleSelectAll() {
    const allIds = similarItems.value.map((it) => it.inspiration.id)
    const allSelected = allIds.length > 0 && allIds.every((id) => batchSelectedIds.value.includes(id))
    batchSelectedIds.value = allSelected ? [] : [...allIds]
  }

  /** 批量添加大标签到勾选的相似素材，成功后刷新相似列表 */
  async function batchAddOutfitTags() {
    if (batchSelectedIds.value.length === 0 || batchTagNames.value.length === 0) return
    batchAdding.value = true
    try {
      const { affected, not_found, skipped_existing } = await batchAddTagsToInspirations(
        batchSelectedIds.value,
        batchTagNames.value,
        'outfit',
        'manual',
      )
      // 明细提示：区分「实际新增」「素材不存在」「关联已存在」
      const parts = [`已为 ${affected} 个相似素材添加大标签`]
      if (not_found > 0) parts.push(`${not_found} 个素材不存在`)
      if (skipped_existing > 0) parts.push(`${skipped_existing} 条关联已存在`)
      Message.success(parts.join('，'))
      exitBatchMode()
      if (detail.value) await refreshSimilar(detail.value.id)
    } catch {
      Message.error('批量添加失败')
    } finally {
      batchAdding.value = false
    }
  }

  return {
    similarItems,
    similarLoading,
    similarSourceLabel,
    loadSimilar,
    refreshSimilar,
    toggleFavoriteSimilar,
    deleteSimilar,
    batchMode,
    batchSelectedIds,
    batchTagNames,
    batchAdding,
    enterBatchMode,
    exitBatchMode,
    toggleSelectSimilar,
    toggleSelectAll,
    batchAddOutfitTags,
  }
}
