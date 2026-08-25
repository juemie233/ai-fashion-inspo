/** 标签合并操作：统一「合并」与「合并并设为别名」的调用、提示与错误处理。
 *
 * 各调用方（标签管理页重复面板、疑似重复对比弹窗等）复用本 composable，
 * 避免 mergeTags + createAlias 的组合逻辑散落多处、提示文案不一致。
 */

import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { createAlias, mergeTags } from '@/api/tags'
import { useTagEvents } from './useTagEvents'

export function useTagMerge() {
  const { notifyTagChanged } = useTagEvents()

  /**
   * 把 source 合并到 target（source 删除，其关联素材迁移到 target）。
   *
   * @param aliasName 提供时，合并后把该名称设为 target 的别名
   * （常用于重复标签合并：保留旧名为别名，使 AI 再识别到旧名时自动归一）
   */
  async function merge(
    sourceId: number,
    targetId: number,
    aliasName?: string,
  ): Promise<{ ok: boolean }> {
    try {
      await mergeTags(sourceId, targetId)
      if (aliasName) {
        await createAlias(targetId, aliasName)
        Message.success(`已合并并将「${aliasName}」设为别名`)
      } else {
        Message.success('已合并')
      }
      notifyTagChanged({ type: 'merged', tagIds: [sourceId], targetId })
      return { ok: true }
    } catch (e) {
      Message.error(getApiErrorMessage(e, '合并失败'))
      return { ok: false }
    }
  }

  return { merge }
}
