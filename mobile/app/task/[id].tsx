/** 任务详情：类型 / 状态 / 进度 / 阶段或摘要 + 逐次运行台账。
 *
 * P0-2：列表只看得到「一行」，排障和判断「到底跑没跑」需要更细的信息：
 * - 当前阶段（`result.stage` + done/total）与暂停说明；
 * - 完成后各业务计数（向量 / 审核 / 去重…）；
 * - 每次执行尝试的耗时、GPU、错误（后端 `GET /api/tasks/{id}/runs`）。
 *
 * 仍然只读：暂停/恢复/删除留在 Web（状态机复杂，误触代价高）。
 */

import { useCallback, useEffect, useState } from 'react'
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View } from 'react-native'
import { useLocalSearchParams } from 'expo-router'
import AppText from '../../components/ui/AppText'
import Badge from '../../components/ui/Badge'
import ErrorState from '../../components/ui/ErrorState'
import EmptyState from '../../components/ui/EmptyState'
import Icon from '../../components/Icon'
import { SkeletonList } from '../../components/ui/Skeleton'
import { fetchTask, fetchTaskRuns, type TaskItem, type TaskRun } from '../../services/api'
import { statusMeta, TASK_STATUS_META, TASK_TYPE_LABELS } from '../../constants/labels'
import {
  describeStage,
  formatRunDuration,
  formatRunTime,
  runStatusLabel,
  summarizeTaskResult,
} from '../../utils/taskPresentation'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

export default function TaskDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const taskId = Number(id)

  const [task, setTask] = useState<TaskItem | null>(null)
  const [runs, setRuns] = useState<TaskRun[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!Number.isFinite(taskId)) {
      setError('任务不存在')
      setLoading(false)
      return
    }
    try {
      const [detail, runList] = await Promise.all([
        fetchTask(taskId),
        fetchTaskRuns(taskId).catch(() => [] as TaskRun[]),
      ])
      setTask(detail)
      setRuns(runList)
      setError('')
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status
      setError(status === 404 ? '任务不存在' : '加载失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [taskId])

  useEffect(() => {
    void load()
  }, [load])

  /** 未结束的任务每 3 秒刷新一次（与列表页同一节奏） */
  const active = Boolean(task && ['pending', 'running'].includes(task.status))
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => {
      void load()
    }, 3000)
    return () => clearInterval(timer)
  }, [active, load])

  const onRefresh = () => {
    setRefreshing(true)
    void load()
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonList rows={4} />
      </View>
    )
  }

  if (!task) {
    return (
      <View style={styles.container}>
        <ErrorState
          title={error || '加载失败'}
          description={error === '任务不存在' ? '这个任务可能已被删除' : '请检查网络或后端服务'}
          onRetry={() => void load()}
        />
      </View>
    )
  }

  const status = statusMeta(TASK_STATUS_META, task.status)
  const typeLabel = TASK_TYPE_LABELS[task.type] ?? task.type
  const stage = describeStage(task)
  const summary = summarizeTaskResult(task)
  const percent =
    typeof task.progress === 'number' && task.progress > 0
      ? Math.min(100, Math.round(task.progress))
      : task.total > 0
        ? Math.min(100, Math.round((task.done / task.total) * 100))
        : 0

  return (
    <FlatList
      style={styles.container}
      data={runs}
      keyExtractor={(run) => String(run.id)}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          colors={[theme.color.brandFill]}
          tintColor={theme.color.brandFill}
        />
      }
      ListHeaderComponent={
        <View style={styles.header}>
          {/* 概要卡 */}
          <View style={styles.card}>
            <View style={styles.titleRow}>
              <AppText variant="title" numberOfLines={2} style={styles.title}>
                {typeLabel}
              </AppText>
              <Badge text={status.label} tone={status.tone} testID="task-detail-status" />
            </View>

            <View style={styles.barTrack}>
              <View style={[styles.barFill, { width: `${percent}%` }]} />
            </View>
            <View style={styles.metaRow}>
              <AppText variant="caption" color="textTertiary" tabularNums>
                进度 {task.done}/{task.total} · {percent}%
              </AppText>
              <AppText variant="caption" color="textTertiary">
                #{task.id}
              </AppText>
            </View>

            {task.error ? (
              <AppText variant="bodySm" color="dangerText">
                {task.error}
              </AppText>
            ) : stage ? (
              <AppText variant="bodySm" color="textSecondary">
                {stage}
              </AppText>
            ) : summary ? (
              <AppText variant="bodySm" color="successText">
                {summary}
              </AppText>
            ) : null}

            <AppText variant="caption" color="textTertiary" numberOfLines={2}>
              创建 {formatRunTime(task.created_at)} · 更新 {formatRunTime(task.updated_at)}
            </AppText>
          </View>

          <AppText variant="subtitle" style={styles.sectionTitle}>
            运行记录
          </AppText>
        </View>
      }
      renderItem={({ item }) => {
        const duration = formatRunDuration(item)
        return (
          <View style={styles.runRow} testID={`run-${item.id}`}>
            <View style={styles.runHeader}>
              <AppText variant="bodySm" color="textSecondary" tabularNums>
                第 {item.attempt + 1} 次 · {runStatusLabel(item.status)}
              </AppText>
              <AppText variant="caption" color="textTertiary" tabularNums>
                {formatRunTime(item.started_at)}
                {duration ? ` · ${duration}` : ''}
              </AppText>
            </View>
            <View style={styles.runMeta}>
              <AppText variant="caption" color="textTertiary" tabularNums>
                {item.total_count > 0
                  ? `${item.item_count}/${item.total_count} 项`
                  : `处理 ${item.item_count} 项`}
              </AppText>
              {item.gpu_model ? (
                <View style={styles.gpuTag}>
                  <Icon name="hardware-chip-outline" size={theme.icon.sm} color={theme.color.textTertiary} />
                  <AppText variant="caption" color="textTertiary" numberOfLines={1}>
                    {item.gpu_model}
                  </AppText>
                </View>
              ) : null}
            </View>
            {item.error ? (
              <AppText variant="caption" color="dangerText" numberOfLines={3}>
                {item.error}
              </AppText>
            ) : null}
          </View>
        )
      }}
      ListEmptyComponent={
        <EmptyState
          icon="time-outline"
          title="还没有运行记录"
          description="任务开始执行后，每次尝试的耗时与结果会出现在这里"
        />
      }
    />
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    list: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    header: { gap: t.space.md },
    card: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
      padding: t.space.lg,
      gap: t.space.sm,
    },
    titleRow: { flexDirection: 'row', alignItems: 'center', gap: t.space.sm },
    title: { flex: 1 },
    barTrack: {
      height: 6,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.bgSubtle,
      overflow: 'hidden',
    },
    barFill: { height: '100%', borderRadius: t.radius.pill, backgroundColor: t.color.brandFill },
    metaRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    sectionTitle: { marginTop: t.space.sm },
    runRow: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.control,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
      padding: t.space.md,
      marginBottom: t.space.sm,
      gap: t.space.xs,
    },
    runHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: t.space.sm },
    runMeta: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: t.space.sm },
    gpuTag: { flexDirection: 'row', alignItems: 'center', gap: 2, flexShrink: 1 },
  })
