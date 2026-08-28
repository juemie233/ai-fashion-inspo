/** 任务类型与状态的中文映射（任务中心专用）。 */

import type { Component } from 'vue'
import {
  IconCheckCircle,
  IconDelete,
  IconFaceSmileFill,
  IconFileImage,
  IconSafe,
  IconScan,
  IconSync,
  IconBarChart,
  IconUser,
  IconUserGroup,
  IconMindMapping,
  IconRelation,
} from '@arco-design/web-vue/es/icon'
import type { UnifiedTask } from '@/types/task'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'

/** 任务类型元数据单一来源：中文标签 / 图标 / 标签色。
 * 任务管理页类型筛选项、任务列表图标与颜色均由本表派生——
 * 新增类型只在此登记一处，避免三张映射表同步维护漏配。
 * 删除类任务刻意使用不同图标：批量删除=垃圾桶、近似重复检测删除=版本对比，
 * 让两类删除任务在列表中一眼可辨。 */
const TASK_TYPE_META: Record<string, { label: string; icon: Component; color: string }> = {
  batch_analyze: { label: '批量 AI 分析', icon: IconFileImage, color: 'arcoblue' },
  quality_check: { label: '质量审核', icon: IconSafe, color: 'orange' },
  batch_delete: { label: '批量删除', icon: IconDelete, color: 'red' },
  deduplicate: { label: '近似重复检测删除', icon: IconSync, color: 'green' },
  scraper: { label: '采集', icon: IconScan, color: 'purple' },
  vector_backfill: { label: '向量回填', icon: IconBarChart, color: 'cyan' },
  face_scan: { label: '人脸库扫描', icon: IconFaceSmileFill, color: 'lime' },
  face_match: { label: '人脸匹配', icon: IconUserGroup, color: 'magenta' },
  face_cluster: { label: '人脸聚合聚类', icon: IconUserGroup, color: 'purple' },
  enrich_blogger_profile: { label: '博主主页补全', icon: IconUser, color: 'gold' },
  tag_health_scan: { label: '标签健康扫描', icon: IconCheckCircle, color: 'pinkpurple' },
  tag_cluster_scan: { label: '标签聚类扫描', icon: IconMindMapping, color: 'cyan' },
  tag_network_analyze: { label: '标签网络分析', icon: IconRelation, color: 'orangered' },
}

/** 任务类型中文标签（与后端 task_queue.type 全量对齐；由 TASK_TYPE_META 派生） */
export const TASK_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(TASK_TYPE_META).map(([k, v]) => [k, v.label]),
)

/** 任务类型对应图标（由 TASK_TYPE_META 派生） */
export const TASK_TYPE_ICONS: Record<string, Component> = Object.fromEntries(
  Object.entries(TASK_TYPE_META).map(([k, v]) => [k, v.icon]),
)

/** 采集平台中文标签（复用来源映射，单一来源避免文案漂移） */
export const SCRAPER_PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: SOURCE_TYPE_LABELS.xiaohongshu,
  douyin: SOURCE_TYPE_LABELS.douyin,
  browser_extension: SOURCE_TYPE_LABELS.browser_extension,
}

/** 任务状态中文标签（success 与 completed 语义一致，统一展示「已完成」） */
export const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '运行中',
  success: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 归一化状态：采集任务的 completed → success */
export function normalizeTaskStatus(status: string): string {
  return status === 'completed' ? 'success' : status
}

/** 状态对应的 Arco 标签预设色 */
export function taskStatusType(status: string): string {
  const map: Record<string, string> = {
    pending: 'gray',
    running: 'arcoblue',
    success: 'green',
    failed: 'red',
    cancelled: 'orange',
  }
  return map[status] || 'gray'
}

/** 任务类型对应的标签颜色（Arco 预设色，由 TASK_TYPE_META 派生） */
export function taskTypeTagColor(type: string): string {
  return TASK_TYPE_META[type]?.color ?? 'gray'
}

// ===== 剩余时间预测 =====

/** 将秒数格式化为中文时长（不足 1 分钟显示秒，否则显示分钟/小时） */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  const s = Math.ceil(seconds)
  if (s < 60) return `${s} 秒`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} 分钟`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm > 0 ? `${h} 小时 ${rm} 分` : `${h} 小时`
}

/** 预测运行中任务的剩余时间（仅采集任务与批量 AI 分析）；信息不足时返回 null */
export function predictEta(task: UnifiedTask): string | null {
  if (task.status !== 'running') return null
  if (task.type !== 'batch_analyze' && task.type !== 'scraper') return null
  const startTs = task.started_at
    ? new Date(task.started_at).getTime()
    : new Date(task.created_at).getTime()
  if (!startTs || Number.isNaN(startTs)) return null
  const elapsedSec = (Date.now() - startTs) / 1000
  if (elapsedSec < 10) return null // 运行时间太短，速率不稳定，先不预测
  const { done, target } = task
  if (done <= 0 || target <= 0 || target <= done) return null
  const rate = done / elapsedSec
  if (rate <= 0) return null
  return formatDuration((target - done) / rate)
}
