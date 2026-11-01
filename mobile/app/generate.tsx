/** 出图记录（只读）：批次列表。
 *
 * 手机侧取舍（docs/移动端.md）：出图依赖 PC 上的 ComfyUI，手机上不做参数
 * 面板与提交，只看「出了什么、要不要用」——点进批次可以看候选图与全屏预览。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、状态用 Badge（令牌色）、时间列不换行。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, View } from 'react-native'
import { useRouter } from 'expo-router'
import AppText from '../components/ui/AppText'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonList } from '../components/ui/Skeleton'
import { fetchGenerateModelLabels, fetchGenerateRuns, type GenerateRun } from '../services/api'
import { modelLabel } from '../utils/modelLabel'
import { statusMeta, GENERATE_STATUS_META } from '../constants/labels'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'

const PAGE_SIZE = 20

export default function GenerateScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)

  const [runs, setRuns] = useState<GenerateRun[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  /** 后端给的最新档位展示名（拉不到时用内置兜底） */
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({})

  /** 翻页互斥 + 请求序号：慢网重复触发 onEndReached 时不再追加重复页 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)

  const load = useCallback(async (nextPage = 1) => {
    if (nextPage > 1 && loadingMoreRef.current) return
    const seq = ++requestSeqRef.current
    if (nextPage > 1) loadingMoreRef.current = true
    try {
      const data = await fetchGenerateRuns(nextPage, PAGE_SIZE)
      if (seq !== requestSeqRef.current) return
      setRuns((prev) => (nextPage === 1 ? data.items : [...prev, ...data.items]))
      setTotal(data.total)
      setPage(nextPage)
      setError('')
    } catch {
      if (seq === requestSeqRef.current) setError('加载失败，请检查网络或后端服务')
    } finally {
      if (nextPage > 1) loadingMoreRef.current = false
      if (seq === requestSeqRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    // 档位名从后端取（单一来源）；失败不打断列表，回退内置表
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

  useEffect(() => {
    void load(1)
  }, [load])

  const onRefresh = () => {
    setRefreshing(true)
    void load(1)
  }

  const onEndReached = () => {
    if (!loading && runs.length < total) void load(page + 1)
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonList rows={5} />
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={runs}
      keyExtractor={(r) => String(r.id)}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          colors={[theme.color.brandFill]}
          tintColor={theme.color.brandFill}
        />
      }
      onEndReached={onEndReached}
      onEndReachedThreshold={0.5}
      renderItem={({ item }) => {
        const status = statusMeta(GENERATE_STATUS_META, item.status)
        return (
          <Pressable
            testID={`run-${item.id}`}
            style={({ pressed }) => [styles.card, pressed && styles.pressed]}
            onPress={() => router.push(`/generate/${item.id}`)}
            accessibilityRole="button"
          >
            <View style={styles.cardHeader}>
              <AppText variant="subtitle" numberOfLines={1} style={styles.model}>
                {modelLabel(item.model_key, modelLabels)}
              </AppText>
              <Badge text={status.label} tone={status.tone} />
            </View>

            <AppText variant="bodySm" color="textSecondary" numberOfLines={2}>
              {item.prompt || '（无描述）'}
            </AppText>

            <View style={styles.footer}>
              <AppText variant="caption" color="textTertiary" tabularNums numberOfLines={1}>
                {item.done}/{item.total} 张
                {item.imported_count > 0 ? ` · 已入库 ${item.imported_count}` : ''}
                {item.files_missing ? ' · 文件缺失' : ''}
              </AppText>
              <AppText variant="caption" color="textTertiary" tabularNums numberOfLines={1}>
                {new Date(item.created_at).toLocaleDateString('zh-CN')}
              </AppText>
            </View>
          </Pressable>
        )
      }}
      ListEmptyComponent={
        error ? (
          <ErrorState onRetry={() => void load(1)} />
        ) : (
          <EmptyState
            icon="sparkles-outline"
            title="还没有出图记录"
            description="出图在电脑端进行，手机上看结果"
          />
        )
      }
      ListFooterComponent={
        runs.length === 0 ? null : (
          <View>
            {runs.length < total ? (
              <ActivityIndicator style={styles.footerSpinner} color={theme.color.brandFill} />
            ) : null}
            <AppText variant="caption" color="textTertiary" style={styles.footerText}>
              共 {total} 个批次
            </AppText>
          </View>
        )
      }
    />
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    list: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    card: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      padding: t.space.md,
      marginBottom: t.space.sm,
      gap: t.space.sm,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
    },
    pressed: { opacity: 0.85 },
    cardHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: t.space.sm },
    model: { flex: 1 },
    footer: { flexDirection: 'row', justifyContent: 'space-between', gap: t.space.sm },
    footerText: { textAlign: 'center', paddingVertical: t.space.md },
    footerSpinner: { paddingVertical: t.space.lg },
  })
