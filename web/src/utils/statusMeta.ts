/** 状态标签的统一元数据：文案 / Arco 预设色 / 自绘 badge 背景色 / 默认 title。
 *
 * 单一来源：任务状态在 utils/taskLabel 既有表上扩展，质量、AI、分析、垃圾桶原因
 * 统一在这里收敛，页面不再各自手写 `a-tag` 颜色映射。
 */

export type StatusTagKind = 'task' | 'quality' | 'ai' | 'analysis' | 'trash'

export interface StatusMeta {
  label: string
  /** Arco tag 预设色 */
  color: string
  /** badge 变体背景色（纯展示用） */
  bg: string
  /** 默认 title（调用方传 title 时优先） */
  title?: string
}

/** 质量审核状态 */
export const QUALITY_STATUS_META: Record<string, StatusMeta> = {
  approved: { label: '已通过', color: 'green', bg: 'rgba(0, 180, 42, 0.85)' },
  pending: { label: '待审核', color: 'orange', bg: 'rgba(160, 160, 160, 0.8)' },
  rejected: {
    label: '已拒绝',
    color: 'red',
    bg: 'rgba(208, 48, 80, 0.85)',
    title: 'AI 判定为非穿搭内容',
  },
}

/** AI 生成标记 */
export function aiStatusMeta(isAi: boolean, sourceType?: string | null): StatusMeta | null {
  if (!isAi) return null
  if (sourceType === 'generated') {
    return {
      label: 'AI 生成',
      color: 'purple',
      bg: 'rgba(122, 80, 200, 0.9)',
      title: '由本地出图生成（不是真实拍摄）',
    }
  }
  return {
    label: '疑似 AI',
    color: 'purple',
    bg: 'rgba(122, 80, 200, 0.9)',
    title: '疑似 AI 生成，请人工确认',
  }
}

/** AI 分析状态 */
export function analysisStatusMeta(status: string | null | undefined): StatusMeta {
  if (status === 'none') return { label: '尚未分析', color: 'gray', bg: 'rgba(160,160,160,.8)' }
  if (status === 'analyzing') {
    return { label: '分析中...', color: 'arcoblue', bg: 'rgba(22,93,255,.8)' }
  }
  if (status === 'error') return { label: '分析失败', color: 'red', bg: 'rgba(208,48,80,.85)' }
  if (status === 'done' || status === 'success') {
    return { label: '已分析', color: 'green', bg: 'rgba(0,180,42,.85)' }
  }
  return { label: status || '未知', color: 'gray', bg: 'rgba(160,160,160,.8)' }
}

/** 垃圾桶删除原因 */
const TRASH_REASON_META: Record<string, StatusMeta> = {
  质量差: { label: '质量差', color: 'red', bg: 'rgba(208,48,80,.85)' },
  重复: { label: '重复', color: 'orange', bg: 'rgba(255,125,0,.85)' },
  不喜欢: { label: '不喜欢', color: 'gray', bg: 'rgba(160,160,160,.8)' },
  隐私: { label: '隐私', color: 'red', bg: 'rgba(208,48,80,.85)' },
  AI生成: { label: 'AI生成', color: 'purple', bg: 'rgba(122,80,200,.9)' },
  其他: { label: '其他', color: 'gray', bg: 'rgba(160,160,160,.8)' },
}

export function trashReasonMeta(reason: string | null | undefined): StatusMeta {
  return (
    TRASH_REASON_META[reason || ''] || {
      label: reason || '未知',
      color: 'gray',
      bg: 'rgba(160,160,160,.8)',
    }
  )
}

export function qualityStatusMeta(status: string | null | undefined): StatusMeta {
  return (
    QUALITY_STATUS_META[status || ''] || {
      label: status || '未知',
      color: 'gray',
      bg: 'rgba(160,160,160,.8)',
    }
  )
}
