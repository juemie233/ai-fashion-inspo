/** 素材库批量多选：勾选状态 + 批量收藏 / 移垃圾桶 / 加标签 / 编辑元数据。 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  batchFavorite as batchFavoriteApi,
  batchTrash as batchTrashApi,
  batchAddTagsToInspirations,
  batchLinkBloggers as batchLinkBloggersApi,
  batchUpdateInspirations,
  type BatchUpdateFields,
} from '@/api/inspirations'

export function useBatchSelection() {
  
  /** 是否处于批量选择模式 */
  const batchMode = ref(false)
  /** 已勾选的素材 ID 集合（整体替换保证响应式） */
  const selectedIds = ref<Set<string>>(new Set())
  /** 已勾选数量 */
  const selectedCount = computed(() => selectedIds.value.size)

  function enterBatchMode() {
    batchMode.value = true
    selectedIds.value = new Set()
  }

  function exitBatchMode() {
    batchMode.value = false
    selectedIds.value = new Set()
  }

  /** 切换单个素材勾选 */
  function toggleSelect(id: string) {
    const next = new Set(selectedIds.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selectedIds.value = next
  }

  /** 全选 / 取消全选当前列表 */
  function toggleSelectAll(allIds: string[]) {
    const allSelected = allIds.length > 0 && allIds.every((id) => selectedIds.value.has(id))
    selectedIds.value = allSelected ? new Set() : new Set(allIds)
  }

  /** 批量收藏 / 取消收藏，返回实际更新数 */
  async function batchFavorite(isFavorite: boolean): Promise<number> {
    const ids = [...selectedIds.value]
    if (ids.length === 0) return 0
    try {
      const updated = await batchFavoriteApi(ids, isFavorite)
      Message.success(`已${isFavorite ? '收藏' : '取消收藏'} ${updated} 个素材`)
      return updated
    } catch {
      Message.error('批量收藏失败')
      return 0
    }
  }

  /** 批量移入垃圾桶，返回实际删除数 */
  async function batchTrash(): Promise<number> {
    const ids = [...selectedIds.value]
    if (ids.length === 0) return 0
    try {
      const { trashed, skipped } = await batchTrashApi(ids)
      const parts = [`已移入垃圾桶 ${trashed} 个`]
      if (skipped > 0) parts.push(`跳过 ${skipped} 个`)
      Message.success(parts.join('，'))
      return trashed
    } catch {
      Message.error('批量删除失败')
      return 0
    }
  }

  /** 批量给勾选素材添加标签 */
  async function batchAddTags(names: string[]) {
    const ids = [...selectedIds.value]
    if (ids.length === 0 || names.length === 0) return
    try {
      const r = await batchAddTagsToInspirations(ids, names, 'free', 'manual')
      const parts = [`已为 ${r.affected} 个素材添加标签`]
      if (r.not_found > 0) parts.push(`${r.not_found} 个素材不存在`)
      if (r.skipped_existing > 0) parts.push(`${r.skipped_existing} 条关联已存在`)
      Message.success(parts.join('，'))
    } catch {
      Message.error('批量添加标签失败')
    }
  }

  /** 批量给勾选素材关联穿搭博主（幂等：已关联自动跳过） */
  async function batchLinkBloggers(personIds: number[]) {
    const ids = [...selectedIds.value]
    if (ids.length === 0 || personIds.length === 0) return
    try {
      const r = await batchLinkBloggersApi(ids, personIds)
      const parts = [`已关联 ${r.linked} 条博主关联`]
      if (r.affected > 0) parts.push(`${r.affected} 个素材`)
      if (r.skipped > 0) parts.push(`跳过已关联 ${r.skipped} 条`)
      if (r.not_found_count > 0) parts.push(`${r.not_found_count} 个素材不存在`)
      Message.success(parts.join('，'))
    } catch {
      Message.error('批量关联博主失败')
    }
  }

  /** 批量编辑元数据（来源 / 收藏 / 审核状态 / 疑似 AI） */
  async function batchUpdate(fields: BatchUpdateFields): Promise<number> {
    const ids = [...selectedIds.value]
    if (ids.length === 0) return 0
    try {
      const updated = await batchUpdateInspirations(ids, fields)
      Message.success(`已更新 ${updated} 个素材`)
      return updated
    } catch {
      Message.error('批量编辑失败')
      return 0
    }
  }

  return {
    batchMode,
    selectedIds,
    selectedCount,
    enterBatchMode,
    exitBatchMode,
    toggleSelect,
    toggleSelectAll,
    batchFavorite,
    batchTrash,
    batchAddTags,
    batchLinkBloggers,
    batchUpdate,
  }
}
