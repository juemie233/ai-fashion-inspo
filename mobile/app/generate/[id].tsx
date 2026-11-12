/** 出图批次详情：候选图网格 + 全屏预览 + **勾选入库 / 丢弃整批**（P1）。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、候选图统一 `MediaCard`（1:1）、
 * 已入库角标用 Badge。
 *
 * P1 补齐「出图在手机上收尾」：
 * - **选择模式**：顶部「选择」进入，点候选图切换勾选（不再直接打开全屏），
 *   底部条显示「入库所选 N 张」；
 * - **确认入库**：`POST /api/generate/runs/{id}/confirm`，返回「入库 N / 跳过 M」，
 *   跳过原因（文件不存在 / 内容重复）原样展示，成功后刷新批次让角标同步；
 * - **丢弃整批**：`POST /api/generate/runs/{id}/cancel` 删除批次与候选目录
 *   （已入库素材不受影响），二次确认后返回上一页。
 *
 * 边界（与规划 §4.2 一致）：不做参数面板 / 提交，不做「单张丢弃」——
 * 后端只有整批丢弃接口，单张丢弃会让列表与目录不一致，宁可整批处理。
 */

import { useCallback, useEffect, useState } from 'react'
import { Alert, FlatList, Pressable, StyleSheet, View, useWindowDimensions } from 'react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'
import MediaCard from '../../components/ui/MediaCard'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import AppText from '../../components/ui/AppText'
import Button from '../../components/ui/Button'
import Icon from '../../components/Icon'
import { SkeletonGrid } from '../../components/ui/Skeleton'
import ImageViewer from '../../components/ImageViewer'
import {
  candidateUrl,
  confirmGenerateCandidates,
  discardGenerateRun,
  fetchGenerateModelLabels,
  fetchGenerateRun,
  type GenerateCandidate,
  type GenerateRun,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { modelLabel } from '../../utils/modelLabel'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { gridColumns } from '../../utils/layout'
import { successFeedback, warningFeedback } from '../../utils/haptics'
import type { Theme } from '../../constants/theme'

/** 缩略图宽度（后端支持 ?w= 生成缩略图，省流量） */
const THUMB_WIDTH = 400

export default function GenerateRunScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)
  const runId = Number(id)
  const { width } = useWindowDimensions()
  const columns = gridColumns(width)

  const [run, setRun] = useState<GenerateRun | null>(null)
  const [candidates, setCandidates] = useState<GenerateCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 全屏预览的图片地址 */
  const [viewerUri, setViewerUri] = useState<string | null>(null)
  /** 后端给的最新档位展示名（拉不到时用内置兜底） */
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({})
  /** 是否处于勾选模式（点击候选图变成勾选） */
  const [selecting, setSelecting] = useState(false)
  /** 已勾选的候选文件名 */
  const [selected, setSelected] = useState<string[]>([])
  /** 入库/丢弃提交中 */
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!Number.isFinite(runId)) {
      setError('批次不存在')
      setLoading(false)
      return
    }
    try {
      const detail = await fetchGenerateRun(runId)
      setRun(detail.run)
      setCandidates(detail.candidates)
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    // 档位名从后端取（单一来源）；失败不打断详情，回退内置表
    let alive = true
    void fetchGenerateModelLabels()
      .then((labels) => {
        if (alive) setModelLabels(labels)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const toggleSelect = (filename: string) => {
    setSelected((prev) =>
      prev.includes(filename) ? prev.filter((f) => f !== filename) : [...prev, filename],
    )
  }

  const exitSelecting = () => {
    setSelecting(false)
    setSelected([])
  }

  /** 入库所选 */
  const handleConfirm = async () => {
    if (selected.length === 0 || busy) return
    setBusy(true)
    try {
      const result = await confirmGenerateCandidates(runId, selected)
      successFeedback()
      const lines = [`入库 ${result.imported} 张`]
      if (result.skipped > 0) {
        const reasons = Array.from(
          new Set(result.skipped_items.map((item) => item.reason).filter(Boolean)),
        )
        lines.push(`跳过 ${result.skipped} 张${reasons.length ? `：${reasons.join('、')}` : ''}`)
      }
      Alert.alert('已入库', lines.join('\n'))
      exitSelecting()
      await load()
    } catch (e) {
      warningFeedback()
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      Alert.alert(
        '入库失败',
        typeof detail === 'string' && detail.trim() ? detail.trim() : '请检查网络或后端服务',
      )
    } finally {
      setBusy(false)
    }
  }

  /** 丢弃整批（删除批次记录与候选目录；已入库素材不受影响） */
  const handleDiscard = () => {
    Alert.alert(
      '丢弃整批',
      '会删除这个批次和它的候选图，已入库的素材不受影响。确定吗？',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '丢弃',
          style: 'destructive',
          onPress: async () => {
            setBusy(true)
            try {
              await discardGenerateRun(runId)
              router.back()
            } catch (e) {
              const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data
                ?.detail
              Alert.alert(
                '丢弃失败',
                typeof detail === 'string' && detail.trim() ? detail.trim() : '请稍后重试',
              )
            } finally {
              setBusy(false)
            }
          },
        },
      ],
    )
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonGrid count={4} columns={2} ratio={1} />
      </View>
    )
  }

  const selectableCount = candidates.filter((c) => !c.imported).length

  return (
    <View style={styles.container}>
      <FlatList
        testID="candidates-list"
        data={candidates}
        keyExtractor={(c) => c.filename}
        numColumns={columns}
        key={`grid-${columns}`}
        columnWrapperStyle={styles.row}
        contentContainerStyle={styles.grid}
        ListHeaderComponent={
          <View style={styles.header}>
            <AppText variant="subtitle" numberOfLines={2}>
              {modelLabel(run?.model_key, modelLabels)}
            </AppText>
            <AppText variant="caption" color="textSecondary" numberOfLines={4}>
              {run?.prompt || '（无描述）'}
            </AppText>
            {run?.negative ? (
              <AppText variant="micro" color="textTertiary" numberOfLines={2}>
                负面词：{run.negative}
              </AppText>
            ) : null}
            <AppText variant="caption" color="textTertiary" tabularNums>
              {run?.done ?? candidates.length}/{run?.total ?? candidates.length} 张
              {run?.imported_count ? ` · 已入库 ${run.imported_count}` : ''}
            </AppText>

            {/* 操作条：选择 / 丢弃整批 */}
            <View style={styles.toolbar}>
              <Pressable
                testID="toggle-select"
                onPress={() => (selecting ? exitSelecting() : setSelecting(true))}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel={selecting ? '退出选择' : '选择候选图'}
                style={styles.toolBtn}
              >
                <Icon
                  name={selecting ? 'close' : 'checkmark-circle-outline'}
                  size={theme.icon.md}
                  color={theme.color.brandText}
                />
                <AppText variant="caption" color="brandText">
                  {selecting ? '退出选择' : '选择'}
                </AppText>
              </Pressable>
              <Pressable
                testID="discard-run"
                onPress={handleDiscard}
                disabled={busy}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel="丢弃整批"
                style={styles.toolBtn}
              >
                <Icon name="trash-outline" size={theme.icon.md} color={theme.color.dangerText} />
                <AppText variant="caption" color="dangerText">
                  丢弃整批
                </AppText>
              </Pressable>
            </View>
          </View>
        }
        renderItem={({ item }) => (
          <MediaCard
            testID={`candidate-${item.index}`}
            uri={candidateUrl(apiBaseUrl, item.url, THUMB_WIDTH)}
            ratio={1}
            cornerBadge={item.imported ? '已入库' : undefined}
            selected={selecting && selected.includes(item.filename)}
            onPress={() =>
              selecting
                ? !item.imported && toggleSelect(item.filename)
                : setViewerUri(candidateUrl(apiBaseUrl, item.url))
            }
            onLongPress={() => {
              if (!selecting && !item.imported) {
                setSelecting(true)
                toggleSelect(item.filename)
              }
            }}
          />
        )}
        ListEmptyComponent={
          error ? (
            <ErrorState onRetry={() => void load()} />
          ) : (
            <EmptyState icon="images-outline" title="这个批次没有候选图" />
          )
        }
      />

      {/* 勾选模式底部操作条 */}
      {selecting ? (
        <View style={styles.selectionBar}>
          <AppText variant="bodySm" color="textSecondary" style={styles.flex} numberOfLines={1}>
            {/* 「都已入库」只在真有候选但都不能选时加；空批次不能这么写（真机发现） */}
            已选 {selected.length} 张
            {selectableCount === 0 && candidates.length > 0 ? '（都已入库）' : ''}
          </AppText>
          <Button title="取消" variant="ghost" onPress={exitSelecting} disabled={busy} />
          <Button
            title={busy ? '入库中…' : `入库所选 ${selected.length} 张`}
            onPress={() => void handleConfirm()}
            loading={busy}
            disabled={selected.length === 0}
            testID="confirm-import"
          />
        </View>
      ) : null}

      <ImageViewer
        visible={viewerUri !== null}
        uri={viewerUri ?? ''}
        onClose={() => setViewerUri(null)}
      />
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    row: { gap: t.space.md, marginBottom: t.space.md },
    header: { marginBottom: t.space.md, gap: t.space.xs },
    toolbar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginTop: t.space.xs,
    },
    toolBtn: { flexDirection: 'row', alignItems: 'center', gap: t.space.xs, minHeight: 36 },
    flex: { flex: 1 },
    selectionBar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.border,
    },
  })
