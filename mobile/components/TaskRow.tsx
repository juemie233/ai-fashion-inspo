/** 后台任务行：展示类型/状态/进度，可取消。 */

import { StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import Icon from './Icon'
import type { TaskItem } from '../services/api'

/** 任务类型中文名（后端 `type` 字段的真实取值，20+ 种；缺失时回退原值） */
const TYPE_LABELS: Record<string, string> = {
  batch_analyze: '批量 AI 分析',
  multi_analyze: '多模态分析',
  image: '图片处理',
  image_gen: '本地出图',
  generated: '出图记录',
  face_scan: '人脸扫描',
  face_match: '人脸匹配',
  face_cluster: '人脸聚类',
  quality_check: '质量审核',
  vector_backfill: '向量回填',
  phash_backfill: '去重回填',
  deduplicate: '内容去重',
  tag_cluster_scan: '标签聚类',
  tag_health_scan: '标签体检',
  tag_network_analyze: '标签网络分析',
  tags: '标签处理',
  batch_delete: '批量删除',
  scraper_tasks: '采集任务',
  bloggers: '博主补全',
  enrich_blogger_profile: '博主资料补全',
  task_event: '任务事件',
  all: '全部',
}

/** 状态文案与配色 */
const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '排队中', color: '#f59e0b' },
  running: { label: '进行中', color: '#6366f1' },
  success: { label: '已完成', color: '#16a34a' },
  failed: { label: '失败', color: '#ef4444' },
  paused: { label: '已暂停', color: '#f59e0b' },
  cancelled: { label: '已取消', color: '#9ca3af' },
}

interface Props {
  task: TaskItem
  onCancel: (id: number) => void
  /** 取消中（禁按钮） */
  cancelling?: boolean
}

export default function TaskRow({ task, onCancel, cancelling = false }: Props) {
  const status = STATUS_META[task.status] ?? { label: task.status || '未知', color: '#9ca3af' }
  const typeLabel = TYPE_LABELS[task.type] ?? task.type ?? '任务'
  const cancellable = task.status === 'pending' || task.status === 'running'
  // 进度兜底：后端 progress 可能缺失，用 done/total 反算
  const ratio =
    typeof task.progress === 'number' && task.progress > 0
      ? Math.min(1, task.progress / 100)
      : task.total > 0
        ? Math.min(1, task.done / task.total)
        : 0

  return (
    <View style={styles.row}>
      <View style={styles.header}>
        <Text style={styles.type}>{typeLabel}</Text>
        <Text style={[styles.status, { color: status.color }]}>{status.label}</Text>
      </View>

      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${Math.round(ratio * 100)}%` }]} />
      </View>

      <View style={styles.footer}>
        <Text style={styles.meta}>
          {task.total > 0 ? `${task.done}/${task.total}` : '—'} · {Math.round(ratio * 100)}%
        </Text>
        {cancellable && (
          <TouchableOpacity
            style={styles.cancelBtn}
            onPress={() => onCancel(task.id)}
            disabled={cancelling}
            hitSlop={8}
          >
            <Icon name="close-circle-outline" size={14} color="#ef4444" />
            <Text style={styles.cancelText}>{cancelling ? '取消中…' : '取消'}</Text>
          </TouchableOpacity>
        )}
      </View>

      {task.error ? <Text style={styles.error}>{task.error}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  row: {
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 12,
    marginHorizontal: 16,
    marginBottom: 8,
  },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  type: { fontSize: 14, fontWeight: '600', color: '#111827' },
  status: { fontSize: 12 },
  barTrack: {
    height: 4,
    borderRadius: 2,
    backgroundColor: '#f3f4f6',
    marginTop: 8,
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 2, backgroundColor: '#6366f1' },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  meta: { fontSize: 11, color: '#9ca3af' },
  cancelBtn: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  cancelText: { fontSize: 12, color: '#ef4444' },
  error: { fontSize: 11, color: '#ef4444', marginTop: 6 },
})
