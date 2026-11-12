/** 后台任务行：类型 / 状态 / 进度 / 阶段或结果摘要，可取消，可点进详情。
 *
 * 审查修复：状态用 `Badge`（实心底 + 白字，11px 也过 4.5:1），类型/状态文案统一走
 * `constants/labels.ts`（改前 `STATUS_META` 在任务页与出图页各一份）。
 *
 * P0-2 升级：
 * - 运行中/排队/暂停给一句话说明（阶段 + 计数），不再只有一个百分比；
 * - 成功后展示结果摘要（如「图像向量 320 · 文本向量 320」）；
 * - 整行可点，进任务详情看阶段与逐次运行台账。
 */

import { Pressable, StyleSheet, View } from 'react-native'
import Icon from './Icon'
import AppText from './ui/AppText'
import Badge from './ui/Badge'
import { useStyles, useTheme } from '../hooks/useTheme'
import { statusMeta, TASK_STATUS_META, TASK_TYPE_LABELS } from '../constants/labels'
import { describeStage, summarizeTaskResult } from '../utils/taskPresentation'
import type { TaskItem } from '../services/api'
import type { Theme } from '../constants/theme'

interface Props {
  task: TaskItem
  onCancel: (id: number) => void
  /** 点击整行进详情（可选：任务详情页自身不传） */
  onPress?: () => void
  /** 取消中（禁按钮） */
  cancelling?: boolean
}

export default function TaskRow({ task, onCancel, onPress, cancelling = false }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const status = statusMeta(TASK_STATUS_META, task.status)
  const typeLabel = TASK_TYPE_LABELS[task.type] ?? task.type ?? '任务'
  // 可取消状态：pending / running / **paused**。
  // 后端 cancel 接口对 paused 是「直接置 cancelled」（暂停任务不占资源，没必要先恢复），
  // 而任务列表把「已暂停」排在**最前**——不给按钮的话用户只能去 Web 端处理。
  const cancellable = ['pending', 'running', 'paused'].includes(task.status)
  // 进度兜底：后端 progress 可能缺失，用 done/total 反算
  const ratio =
    typeof task.progress === 'number' && task.progress > 0
      ? Math.min(1, task.progress / 100)
      : task.total > 0
        ? Math.min(1, task.done / task.total)
        : 0
  const percent = Math.round(ratio * 100)
  /** 运行中/排队/暂停的阶段说明 */
  const stage = describeStage(task)
  /** 已结束任务的结果摘要 */
  const summary = summarizeTaskResult(task)

  return (
    <Pressable
      testID={`task-${task.id}`}
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      accessibilityLabel={onPress ? `查看任务详情：${typeLabel}` : undefined}
      style={({ pressed }) => [styles.row, pressed && onPress ? styles.pressed : null]}
    >
      <View style={styles.header}>
        <AppText variant="subtitle" numberOfLines={1} style={styles.type}>
          {typeLabel}
        </AppText>
        <Badge text={status.label} tone={status.tone} />
        {onPress ? (
          <Icon name="chevron-forward" size={theme.icon.sm} color={theme.color.textDisabled} />
        ) : null}
      </View>

      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${percent}%` }]} />
      </View>

      <View style={styles.footer}>
        <AppText variant="caption" color="textTertiary" tabularNums>
          {task.total > 0 ? `${task.done}/${task.total}` : '—'} · {percent}%
        </AppText>
        {cancellable ? (
          <Pressable
            style={styles.cancelBtn}
            onPress={() => onCancel(task.id)}
            disabled={cancelling}
            hitSlop={10}
            accessibilityRole="button"
            accessibilityLabel="取消任务"
          >
            <Icon name="close-circle-outline" size={theme.icon.sm} color={theme.color.dangerText} />
            <AppText variant="caption" color="dangerText">
              {cancelling ? '取消中…' : '取消'}
            </AppText>
          </Pressable>
        ) : null}
      </View>

      {/* 阶段（未结束）/ 摘要（已完成）/ 错误（失败）三选一 */}
      {task.error ? (
        <AppText variant="caption" color="dangerText" numberOfLines={2}>
          {task.error}
        </AppText>
      ) : stage ? (
        <AppText variant="caption" color="textSecondary" numberOfLines={2}>
          {stage}
        </AppText>
      ) : summary ? (
        <AppText variant="caption" color="successText" numberOfLines={2}>
          {summary}
        </AppText>
      ) : null}
    </Pressable>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    row: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      padding: t.space.md,
      marginHorizontal: t.space.lg,
      marginBottom: t.space.sm,
      gap: t.space.sm,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
    },
    pressed: { backgroundColor: t.color.bgSubtle },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: t.space.sm,
    },
    type: { flex: 1 },
    barTrack: {
      height: 4,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.bgSubtle,
      overflow: 'hidden',
    },
    barFill: { height: '100%', borderRadius: t.radius.pill, backgroundColor: t.color.brandFill },
    footer: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    cancelBtn: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  })
