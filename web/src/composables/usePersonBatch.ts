/** 人物管理批量操作：多选、批量纳入追踪/删除、头像覆盖率与批量清除、同名建议。
 *
 * 为什么单独一个 composable：列表本身的加载/排序/列定义在 usePersonList，而「多选之后
 * 干什么」是一组独立的破坏性/批量动作（含确认、结果汇报、刷新）；混在一起会让列表
 * 逻辑继续膨胀。破坏性操作的结果都**逐条汇报**（跳过/被挡下的原因），不静默吞掉。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  bloggersApi,
  type AvatarCoverage,
  type DuplicateNameGroup,
  type PersonBatchSkip,
} from '@/api/persons'
import { getApiErrorMessage } from '@/utils/apiError'

/** 人物种类：只有博主有「自动登记 / 头像 / 同名」这些能力（仅本模块使用） */
type PersonBatchKind = 'blogger' | 'model'

export function usePersonBatch(kind: PersonBatchKind, afterMutate: () => void | Promise<void>) {
  /** 表格多选的 key（Arco row-selection） */
  const selectedKeys = ref<(string | number)[]>([])
  /** 已选数量 */
  const selectedCount = computed(() => selectedKeys.value.length)
  /** 批量动作进行中（按钮转圈，防连点） */
  const busy = ref(false)

  /** 头像覆盖率（仅博主；加载失败时卡片不显示，不打扰） */
  const coverage = ref<AvatarCoverage | null>(null)
  /** 同名建议分组 */
  const duplicateGroups = ref<DuplicateNameGroup[]>([])
  /** 同名建议弹窗开关 / 加载中 */
  const duplicateOpen = ref(false)
  const duplicateLoading = ref(false)

  /** 把第一条跳过原因拼进提示（多条时只说第一条，避免刷屏） */
  function firstReason(items: PersonBatchSkip[]): string {
    return items.length > 0 ? `（${items[0].name}：${items[0].reason}）` : ''
  }

  /** 选中变化（Arco selection-change 回调） */
  function onSelectionChange(keys: (string | number)[]): void {
    selectedKeys.value = keys
  }

  /** 清空选择（批量动作完成后调用，避免误触第二次） */
  function clearSelection(): void {
    selectedKeys.value = []
  }

  /** 拉取头像覆盖率（仅博主） */
  async function loadCoverage(): Promise<void> {
    if (kind !== 'blogger') return
    try {
      coverage.value = await bloggersApi.avatarCoverage()
    } catch {
      // 覆盖率只是展示用，失败就不显示卡片
    }
  }

  /** 批量纳入追踪（只对「自动登记」的博主生效） */
  async function batchPromote(): Promise<void> {
    const ids = selectedKeys.value.map(Number)
    if (ids.length === 0) return
    busy.value = true
    try {
      const result = await bloggersApi.batchPromote(ids)
      if (result.promoted > 0) {
        Message.success(
          `已纳入追踪 ${result.promoted} 位` +
            (result.skipped.length > 0
              ? `，${result.skipped.length} 位未处理${firstReason(result.skipped)}`
              : ''),
        )
      } else {
        Message.warning(`没有可纳入追踪的博主${firstReason(result.skipped)}`)
      }
      clearSelection()
      await afterMutate()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量纳入追踪失败'))
    } finally {
      busy.value = false
    }
  }

  /** 批量删除（有素材关联的会被后端挡下） */
  async function batchDelete(): Promise<void> {
    const ids = selectedKeys.value.map(Number)
    if (ids.length === 0) return
    busy.value = true
    try {
      const result = await bloggersApi.batchDelete(ids)
      if (result.blocked.length === 0) {
        Message.success(`已删除 ${result.deleted} 位`)
      } else {
        Message.warning(
          `已删除 ${result.deleted} 位；${result.blocked.length} 位未删除` +
            firstReason(result.blocked),
        )
      }
      clearSelection()
      await afterMutate()
      await loadCoverage()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '批量删除失败'))
    } finally {
      busy.value = false
    }
  }

  /** 清除「所选博主」的头像（只清有头像的） */
  async function clearSelectedAvatars(): Promise<void> {
    const ids = selectedKeys.value.map(Number)
    if (ids.length === 0) return
    busy.value = true
    try {
      const result = await bloggersApi.clearAvatars(ids)
      if (result.cleared > 0) {
        Message.success(`已清除 ${result.cleared} 个头像`)
      } else {
        Message.info('所选博主都没有头像')
      }
      clearSelection()
      await afterMutate()
      await loadCoverage()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '清除头像失败'))
    } finally {
      busy.value = false
    }
  }

  /** 打开同名建议弹窗并拉数据 */
  async function openDuplicates(): Promise<void> {
    duplicateOpen.value = true
    duplicateLoading.value = true
    try {
      const data = await bloggersApi.duplicateNames()
      duplicateGroups.value = data.groups
    } catch (e) {
      Message.error(getApiErrorMessage(e, '加载同名建议失败'))
      duplicateGroups.value = []
    } finally {
      duplicateLoading.value = false
    }
  }

  /**
   * 把一组同名账号合并为同一人（人物组）：以素材数最多者为组主。
   *
   * @param group 同名建议的一组
   * @returns 是否成功（失败已提示）
   */
  async function mergeGroup(group: DuplicateNameGroup): Promise<boolean> {
    if (group.items.length < 2) return false
    const [primary, ...others] = group.items
    busy.value = true
    try {
      for (const other of others) {
        await bloggersApi.linkGroup({
          blogger_id: other.id,
          target_blogger_id: primary.id,
        })
      }
      Message.success(`已合并「${primary.name}」的 ${others.length} 个同名账号为同一人`)
      duplicateGroups.value = duplicateGroups.value.filter((item) => item.key !== group.key)
      await afterMutate()
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '合并为同一人失败'))
      return false
    } finally {
      busy.value = false
    }
  }

  /** 删除同名组里「没有任何素材」的空壳账号（其余受后端校验保护） */
  async function removeEmptyAccounts(group: DuplicateNameGroup): Promise<void> {
    const emptyIds = group.items
      .filter((item) => item.inspiration_count === 0)
      .map((item) => item.id)
    if (emptyIds.length === 0) {
      Message.info('这组账号都还有素材，不能直接删除')
      return
    }
    busy.value = true
    try {
      const result = await bloggersApi.batchDelete(emptyIds)
      Message.success(
        `已删除 ${result.deleted} 个空壳账号` +
          (result.blocked.length > 0 ? `，${result.blocked.length} 个未删除` : ''),
      )
      duplicateGroups.value = duplicateGroups.value
        .map((item) => ({
          ...item,
          items: item.items.filter((row) => !emptyIds.includes(row.id)),
        }))
        .filter((item) => item.items.length > 1)
      await afterMutate()
      await loadCoverage()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '删除空壳账号失败'))
    } finally {
      busy.value = false
    }
  }

  return {
    selectedKeys,
    selectedCount,
    busy,
    coverage,
    duplicateGroups,
    duplicateOpen,
    duplicateLoading,
    onSelectionChange,
    clearSelection,
    loadCoverage,
    batchPromote,
    batchDelete,
    clearSelectedAvatars,
    openDuplicates,
    mergeGroup,
    removeEmptyAccounts,
  }
}
