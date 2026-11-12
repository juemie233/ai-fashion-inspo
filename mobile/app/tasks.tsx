/** 后台任务页：进度 / 阶段 / 结果摘要，可筛选、翻页、取消；点行看详情。
 *
 * 设计取舍（见 docs/mobile/移动端.md）：暂停/恢复/删除留在 Web ——
 * 状态机复杂，手机上误触代价高。
 *
 * P0-2 升级：
 * - 触底翻页（原固定只拉前 30 条，长任务根本翻不到）；
 * - 状态 chip + 类型弹层筛选（后端 `/api/tasks` 原生支持 status/type）；
 * - 每行显示阶段或结果摘要（`TaskRow`），点行进详情看运行台账；
 * - 自动刷新不再是「静默重绘」：只更新第一页并保留已加载的后续页，滚动位置不跳。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import { cancelTask, fetchTasks, type TaskItem, type TaskListFilter } from '../services/api'
import TaskRow from '../components/TaskRow'
import AppText from '../components/ui/AppText'
import Button from '../components/ui/Button'
import Chip from '../components/ui/Chip'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import BottomSheet from '../components/ui/BottomSheet'
import Icon from '../components/Icon'
import { SkeletonList } from '../components/ui/Skeleton'
import { useStyles, useTheme } from '../hooks/useTheme'
import { TASK_STATUS_FILTERS } from '../utils/taskPresentation'
import { TASK_TYPE_LABELS } from '../constants/labels'
import type { Theme } from '../constants/theme'

const PAGE_SIZE = 20

/** 类型筛选弹层的数据源（17 种，按中文名排序，便于查找） */
const TYPE_OPTIONS = Object.entries(TASK_TYPE_LABELS)
  .map(([key, label]) => ({ key, label }))
  .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))

export default function TasksScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)

  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [cancellingId, setCancellingId] = useState<number | null>(null)

  /** 当前筛选（状态 chip + 类型弹层） */
  const [statusKey, setStatusKey] = useState('all')
  const [typeKey, setTypeKey] = useState<string | null>(null)
  const [typeSheetOpen, setTypeSheetOpen] = useState(false)

  /** 翻页互斥 + 请求序号：慢网下重复触发 onEndReached 不追加重复页，切筛选时旧响应作废 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)

  const buildFilter = useCallback(
    (): TaskListFilter => {
      const next: TaskListFilter = {}
      const status = TASK_STATUS_FILTERS.find((f) => f.key === statusKey)?.status
      if (status) next.status = status
      if (typeKey) next.type = typeKey
      return next
    },
    [statusKey, typeKey],
  )
  /** 每次请求都现取一份（只含已选条件） */
  const filter = buildFilter()
  /** 自动刷新时不要重新创建 filter 对象导致 effect 反复重跑 */
  const filterKey = `${statusKey}|${typeKey ?? ''}`

  /** 拉第一页（替换列表） */
  const load = useCallback(
    async (nextFilter: TaskListFilter) => {
      const seq = ++requestSeqRef.current
      try {
        const data = await fetchTasks(1, PAGE_SIZE, nextFilter)
        if (seq !== requestSeqRef.current) return
        setTasks(Array.isArray(data?.items) ? data.items : [])
        setTotal(data?.total ?? 0)
        setPage(1)
        setError('')
      } catch {
        if (seq === requestSeqRef.current) setError('加载失败，请检查网络或后端服务')
      } finally {
        if (seq === requestSeqRef.current) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    },
    [],
  )

  /** 翻页（追加） */
  const loadMore = useCallback(async (nextFilter: TaskListFilter, nextPage: number) => {
    if (loadingMoreRef.current) return
    loadingMoreRef.current = true
    setLoadingMore(true)
    const seq = ++requestSeqRef.current
    try {
      const data = await fetchTasks(nextPage, PAGE_SIZE, nextFilter)
      if (seq !== requestSeqRef.current) return
      setTasks((prev) => {
        const seen = new Set(prev.map((t) => t.id))
        return [...prev, ...(data?.items ?? []).filter((t) => !seen.has(t.id))]
      })
      setTotal(data?.total ?? 0)
      setPage(nextPage)
    } catch {
      if (seq === requestSeqRef.current) setError('加载更多失败，请下拉重试')
    } finally {
      loadingMoreRef.current = false
      if (seq === requestSeqRef.current) setLoadingMore(false)
    }
  }, [])

  // 首次进入 / 切筛选：重置并拉第一页
  useEffect(() => {
    setLoading(true)
    setTasks([])
    setPage(1)
    void load(buildFilter())
    // filterKey 已覆盖 statusKey/typeKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, load])

  /** 是否有排队/进行中的任务：有就自动刷新，没就停（避免空转） */
  const hasActive = tasks.some((t) => t.status === 'pending' || t.status === 'running')

  useEffect(() => {
    if (!hasActive) return
    // 从上传页点「查看分析进度」进来就是为了看进度，只靠下拉刷新的话进度条不会动。
    // 自动刷新只拉第一页并按 id 合并：已加载的后续页不会丢、滚动位置也不会跳。
    const timer = setInterval(() => {
      void (async () => {
        const seq = ++requestSeqRef.current
        try {
          const data = await fetchTasks(1, PAGE_SIZE, filter)
          if (seq !== requestSeqRef.current) return
          const fresh = Array.isArray(data?.items) ? data.items : []
          setTasks((prev) => {
            const knownIds = new Set(prev.map((t) => t.id))
            // 新冒出来的任务插到最前（后端按 id 倒序），已有的就地更新进度
            const added = fresh.filter((t) => !knownIds.has(t.id))
            const updated = prev.map((t) => fresh.find((f) => f.id === t.id) ?? t)
            return [...added, ...updated]
          })
          setTotal(data?.total ?? 0)
        } catch {
          // 自动刷新失败静默：下拉刷新会给出明确错误
        }
      })()
    }, 3000)
    return () => clearInterval(timer)
    // filter 由 filterKey 决定，这里只依赖 key，避免每次渲染都重建定时器
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActive, filterKey])

  const handleRefresh = () => {
    setRefreshing(true)
    void load(filter)
  }

  const handleEndReached = () => {
    if (loading || loadingMore || tasks.length >= total) return
    void loadMore(filter, page + 1)
  }

  const handleCancel = (id: number) => {
    Alert.alert('取消任务', '确定要取消这个任务吗？', [
      { text: '再想想', style: 'cancel' },
      {
        text: '确定取消',
        style: 'destructive',
        onPress: async () => {
          setCancellingId(id)
          try {
            await cancelTask(id)
            await load(filter)
          } catch {
            Alert.alert('取消失败', '请稍后重试')
          } finally {
            setCancellingId(null)
          }
        },
      },
    ])
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonList rows={5} />
      </View>
    )
  }

  const typeLabel = typeKey ? (TASK_TYPE_LABELS[typeKey] ?? typeKey) : '全部类型'

  return (
    <View style={styles.container}>
      {/* 筛选：状态 chip（横滑）+ 类型弹层 */}
      <View style={styles.filterBar}>
        <FlatList
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.statusChips}
          contentContainerStyle={styles.statusChipsContent}
          data={TASK_STATUS_FILTERS}
          keyExtractor={(f) => f.key}
          renderItem={({ item }) => (
            <Chip
              testID={`task-status-${item.key}`}
              label={item.label}
              selected={item.key === statusKey}
              onPress={() => setStatusKey(item.key)}
            />
          )}
        />
        <Pressable
          testID="task-type-filter"
          onPress={() => setTypeSheetOpen(true)}
          accessibilityRole="button"
          accessibilityLabel={`类型筛选：${typeLabel}`}
          style={({ pressed }) => [styles.typeBtn, pressed && styles.pressed]}
        >
          <AppText variant="caption" color="textSecondary" numberOfLines={1}>
            {typeLabel}
          </AppText>
          <Icon name="chevron-down" size={theme.icon.sm} color={theme.color.textSecondary} />
        </Pressable>
      </View>

      <FlatList
        testID="task-list"
        data={tasks}
        keyExtractor={(t) => String(t.id)}
        contentContainerStyle={styles.list}
        onEndReached={handleEndReached}
        onEndReachedThreshold={0.5}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={handleRefresh}
            colors={[theme.color.brandFill]}
            tintColor={theme.color.brandFill}
          />
        }
        renderItem={({ item }) => (
          <TaskRow
            task={item}
            onCancel={handleCancel}
            cancelling={cancellingId === item.id}
            onPress={() => router.push(`/task/${item.id}`)}
          />
        )}
        ListHeaderComponent={
          hasActive ? (
            <View style={styles.autoRefresh}>
              <ActivityIndicator size="small" color={theme.color.brandFill} />
              <AppText variant="caption" color="textTertiary">
                每 3 秒自动刷新，完成或取消后自动停止
              </AppText>
            </View>
          ) : null
        }
        ListEmptyComponent={
          error ? (
            <ErrorState onRetry={() => void load(filter)} />
          ) : (
            <EmptyState
              icon="pulse-outline"
              title={statusKey === 'all' && !typeKey ? '暂无后台任务' : '没有符合条件的任务'}
              description={
                statusKey === 'all' && !typeKey
                  ? '上传或采集素材后，AI 分析会出现在这里'
                  : '换个状态或类型再看看'
              }
            />
          )
        }
        ListFooterComponent={
          tasks.length === 0 ? null : loadingMore ? (
            <ActivityIndicator style={styles.footer} color={theme.color.brandFill} />
          ) : tasks.length < total ? (
            <Button
              title={`加载更多（${tasks.length}/${total}）`}
              variant="ghost"
              onPress={() => void loadMore(filter, page + 1)}
              style={styles.loadMoreBtn}
            />
          ) : (
            <AppText variant="caption" color="textTertiary" style={styles.footerText}>
              共 {total} 个任务
            </AppText>
          )
        }
      />

      {/* 类型筛选弹层 */}
      <BottomSheet
        visible={typeSheetOpen}
        title="按类型筛选"
        onClose={() => setTypeSheetOpen(false)}
        testID="task-type-sheet"
      >
        <FlatList
          data={[{ key: '__all__', label: '全部类型' }, ...TYPE_OPTIONS]}
          keyExtractor={(item) => item.key}
          style={styles.typeList}
          initialNumToRender={TYPE_OPTIONS.length + 1}
          renderItem={({ item }) => {
            const active = item.key === '__all__' ? typeKey === null : typeKey === item.key
            return (
              <Pressable
                testID={`task-type-${item.key}`}
                onPress={() => {
                  setTypeKey(item.key === '__all__' ? null : item.key)
                  setTypeSheetOpen(false)
                }}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                style={({ pressed }) => [styles.typeRow, pressed && styles.pressed]}
              >
                <AppText variant="body" color={active ? 'brandText' : 'textPrimary'}>
                  {item.label}
                </AppText>
                {active ? (
                  <Icon name="checkmark" size={theme.icon.md} color={theme.color.brandText} />
                ) : null}
              </Pressable>
            )
          }}
        />
      </BottomSheet>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    filterBar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    statusChips: { flex: 1 },
    statusChipsContent: { gap: t.space.sm, alignItems: 'center', paddingRight: t.space.xs },
    typeBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 2,
      minHeight: t.touch,
      paddingLeft: t.space.xs,
      maxWidth: 120,
    },
    pressed: { opacity: 0.7 },
    list: { paddingVertical: t.space.md, flexGrow: 1 },
    autoRefresh: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
      paddingBottom: t.space.sm,
    },
    footer: { paddingVertical: t.space.lg },
    footerText: { textAlign: 'center', paddingVertical: t.space.md },
    loadMoreBtn: { alignSelf: 'center', marginVertical: t.space.sm },
    typeList: { maxHeight: 420 },
    typeRow: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: t.space.lg,
    },
  })
