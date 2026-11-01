/** 合集详情：合集内素材的两列网格（分页加载）。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、卡片统一 `MediaCard`（3:4、圆角 14）。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View } from 'react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'
import MediaCard from '../../components/ui/MediaCard'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import { SkeletonGrid } from '../../components/ui/Skeleton'
import { fetchCollectionInspirations, getFileUrl, type Inspiration } from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { sourceLabel } from '../../utils/sourceLabel'
import type { Theme } from '../../constants/theme'

const PAGE_SIZE = 30

export default function CollectionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const [items, setItems] = useState<Inspiration[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  const collectionId = Number(id)

  /** 请求序号：下拉刷新会让在飞的翻页请求作废，避免旧页追加到新列表后面 */
  const requestSeqRef = useRef(0)
  /** 翻页互斥：上一页还没回来时 onEndReached 重复触发不再发同一页 */
  const loadingMoreRef = useRef(false)

  const load = useCallback(
    async (nextPage = 1) => {
      if (!Number.isFinite(collectionId)) {
        setError('合集不存在')
        setLoading(false)
        return
      }
      if (nextPage > 1 && loadingMoreRef.current) return
      const seq = ++requestSeqRef.current
      if (nextPage > 1) loadingMoreRef.current = true
      try {
        const data = await fetchCollectionInspirations(collectionId, nextPage, PAGE_SIZE)
        if (seq !== requestSeqRef.current) return
        setItems((prev) => (nextPage === 1 ? data.items : [...prev, ...data.items]))
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
    },
    [collectionId],
  )

  useEffect(() => {
    void load(1)
  }, [load])

  const onRefresh = () => {
    setRefreshing(true)
    void load(1)
  }

  const onEndReached = () => {
    if (!loading && items.length < total) void load(page + 1)
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonGrid count={6} columns={2} />
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={items}
      keyExtractor={(item) => item.id}
      numColumns={2}
      columnWrapperStyle={styles.row}
      contentContainerStyle={styles.grid}
      onEndReached={onEndReached}
      onEndReachedThreshold={0.5}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          colors={[theme.color.brandFill]}
          tintColor={theme.color.brandFill}
        />
      }
      renderItem={({ item }) => (
        <MediaCard
          uri={
            item.thumbnail_path
              ? getFileUrl(apiBaseUrl, item.thumbnail_path)
              : getFileUrl(apiBaseUrl, item.file_path)
          }
          badgeText={sourceLabel(item.source_type)}
          video={item.media_type === 'video'}
          onPress={() => router.push(`/detail/${item.id}`)}
        />
      )}
      ListEmptyComponent={
        error ? (
          <ErrorState onRetry={() => void load(1)} />
        ) : (
          <EmptyState icon="images-outline" title="合集里还没有素材" />
        )
      }
      ListFooterComponent={
        items.length < total ? (
          <ActivityIndicator style={styles.footer} color={theme.color.brandFill} />
        ) : null
      }
    />
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    row: { gap: t.space.md, marginBottom: t.space.md },
    footer: { paddingVertical: t.space.lg },
  })
