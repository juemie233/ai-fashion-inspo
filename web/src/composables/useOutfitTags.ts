/** 穿搭大标签：已有标签加载、手动添加、AI 建议与一键入库。 */

import { ref } from 'vue'
import { getApiErrorMessage } from '@/utils/apiError'
import type { Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  fetchInspiration,
  addTagsToInspiration,
  removeTagFromInspiration,
  suggestOutfitTags,
  type InspirationDetailOut,
  type InspirationTagOut,
} from '@/api/inspirations'
import { fetchTagsGrouped } from '@/api/tags'

/** 大标签下拉选项（label/value 结构，供 n-select 使用） */
export interface OutfitTagOption {
  label: string
  value: string
}

/**
 * 穿搭大标签相关状态与逻辑。
 * @param detail 素材详情 ref（可能为 null），供内部刷新详情数据
 */
export function useOutfitTags(detail: Ref<InspirationDetailOut | null>) {
  
  const outfitTagOptions = ref<OutfitTagOption[]>([])
  const outfitSelected = ref<string[]>([])
  const outfitAdding = ref(false)
  const aiSuggesting = ref(false)
  const aiSuggestions = ref<string[]>([])

  /** 当前素材的穿搭大标签 */
  function outfitTags(): InspirationTagOut[] {
    if (!detail.value) return []
    return detail.value.tags.filter((t) => t.tag.category === 'outfit')
  }

  /** 刷新详情：仅在用户仍停留在同一素材时写回，防止切换素材后被旧详情覆盖 */
  async function _refreshDetail(id: string) {
    const fresh = await fetchInspiration(id)
    if (detail.value?.id === id) detail.value = fresh
  }

  /** 加载已有大标签作为选择项 */
  async function loadOutfitOptions() {
    try {
      const groups = await fetchTagsGrouped()
      const outfit = groups.find((g) => g.category === 'outfit')
      outfitTagOptions.value = (outfit?.tags || []).map((t) => ({ label: t.name, value: t.name }))
    } catch {
      /* 静默 */
    }
  }

  /** 手动添加大标签（可多选，可从已有标签中选择或输入新建） */
  async function addOutfitTags() {
    if (!detail.value || outfitSelected.value.length === 0) return
    outfitAdding.value = true
    try {
      await addTagsToInspiration(detail.value.id, outfitSelected.value, 'outfit', 'manual')
      outfitSelected.value = []
      Message.success('已添加大标签')
      await _refreshDetail(detail.value.id)
      loadOutfitOptions()
    } catch {
      Message.error('添加失败')
    } finally {
      outfitAdding.value = false
    }
  }

  /** 输入新大标签后按两次回车快速添加：第二次回车（输入框已空且有待添加标签）触发添加 */
  function onOutfitEnter(e: KeyboardEvent) {
    const inputText = (e.target as HTMLInputElement | null)?.value?.trim() ?? ''
    if (inputText === '' && outfitSelected.value.length > 0 && !outfitAdding.value) {
      e.preventDefault()
      e.stopPropagation()
      addOutfitTags()
    }
  }

  /** 删除大标签 */
  async function removeOutfitTag(tagId: number) {
    if (!detail.value) return
    try {
      await removeTagFromInspiration(detail.value.id, tagId)
      await _refreshDetail(detail.value.id)
      Message.success('已移除大标签')
    } catch {
      Message.error('移除失败')
    }
  }

  /** AI 建议大标签（只建议不入库） */
  async function aiSuggestOutfitTags() {
    if (!detail.value) return
    aiSuggesting.value = true
    aiSuggestions.value = []
    try {
      const data = await suggestOutfitTags(detail.value.id)
      aiSuggestions.value = data.suggestions || []
      if (aiSuggestions.value.length === 0) {
        Message.info('AI 认为该穿搭不够有特色，未给出大标签建议')
      }
    } catch (e) {
      Message.error(getApiErrorMessage(e, 'AI 建议失败'))
    } finally {
      aiSuggesting.value = false
    }
  }

  /** 确认入库某条 AI 建议 */
  async function confirmOutfitTag(name: string) {
    if (!detail.value) return
    try {
      await addTagsToInspiration(detail.value.id, [name], 'outfit', 'ai_generated')
      aiSuggestions.value = aiSuggestions.value.filter((s) => s !== name)
      await _refreshDetail(detail.value.id)
      Message.success(`已添加「${name}」`)
    } catch {
      Message.error('添加失败')
    }
  }

  /** 一键确认全部 AI 建议入库 */
  async function confirmAllOutfitTags() {
    if (!detail.value || aiSuggestions.value.length === 0) return
    const names = [...aiSuggestions.value]
    try {
      await addTagsToInspiration(detail.value.id, names, 'outfit', 'ai_generated')
      aiSuggestions.value = []
      await _refreshDetail(detail.value.id)
      Message.success(`已全部入库 ${names.length} 个大标签`)
    } catch {
      Message.error('批量入库失败')
    }
  }

  /** 丢弃某条 AI 建议 */
  function dismissOutfitTag(name: string) {
    aiSuggestions.value = aiSuggestions.value.filter((s) => s !== name)
  }

  return {
    outfitTagOptions,
    outfitSelected,
    outfitAdding,
    aiSuggesting,
    aiSuggestions,
    outfitTags,
    loadOutfitOptions,
    addOutfitTags,
    onOutfitEnter,
    removeOutfitTag,
    aiSuggestOutfitTags,
    confirmOutfitTag,
    confirmAllOutfitTags,
    dismissOutfitTag,
  }
}
