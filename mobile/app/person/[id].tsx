/** 人物作品墙：头部资料 + 该人物素材的分页网格。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、卡片统一 `MediaCard`、
 * 平台中文名与分类统一走 `constants/labels.ts`。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, View, useWindowDimensions } from 'react-native'
import { Image } from 'expo-image'
import { useLocalSearchParams, useRouter } from 'expo-router'
import Icon from '../../components/Icon'
import AppText from '../../components/ui/AppText'
import MediaCard from '../../components/ui/MediaCard'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import { SkeletonGrid } from '../../components/ui/Skeleton'
import {
  fetchPerson,
  fetchPersonInspirations,
  getFileUrl,
  type Inspiration,
  type Person,
  type PersonKind,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { gridColumns } from '../../utils/layout'
import { platformLabel } from '../../constants/labels'
import { dominantColor } from '../../utils/dominantColor'
import type { Theme } from '../../constants/theme'

const PAGE_SIZE = 30

export default function PersonDetailScreen() {
  const { id, kind } = useLocalSearchParams<{ id: string; kind?: string }>()
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const personKind: PersonKind = kind === 'model' ? 'model' : 'blogger'
  const personId = Number(id)
  const { width } = useWindowDimensions()
  const columns = gridColumns(width)

  const [person, setPerson] = useState<Person | null>(null)
  const [items, setItems] = useState<Inspiration[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  /** 翻页互斥 + 请求序号：避免慢网重复触发 onEndReached 时追加重复页 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)

  /** 人物资料：直接按 id 取详情（不再从列表前 200 条里找） */
  const loadPerson = useCallback(async () => {
    try {
      setPerson(await fetchPerson(personKind, personId))
    } catch {
      // 资料失败不影响作品墙
    }
  }, [personKind, personId])

  const loadWorks = useCallback(
    async (nextPage = 1) => {
      if (!Number.isFinite(personId)) {
        setError('人物不存在')
        setLoading(false)
        return
      }
      if (nextPage > 1 && loadingMoreRef.current) return
      const seq = ++requestSeqRef.current
      if (nextPage > 1) loadingMoreRef.current = true
      try {
        const data = await fetchPersonInspirations(personKind, personId, nextPage, PAGE_SIZE)
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
    [personKind, personId],
  )

  useEffect(() => {
    void loadPerson()
    void loadWorks(1)
  }, [loadPerson, loadWorks])

  const onRefresh = () => {
    setRefreshing(true)
    void loadPerson()
    void loadWorks(1)
  }

  const onEndReached = () => {
    if (!loading && items.length < total) void loadWorks(page + 1)
  }

  if (loading && items.length === 0) {
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
      ListHeaderComponent={
        <View style={styles.header}>
          <View style={styles.headerTop}>
            {person?.avatar_path ? (
              <Image
                source={{ uri: getFileUrl(apiBaseUrl, person.avatar_path) }}
                style={styles.avatar}
                contentFit="cover"
                transition={150}
                cachePolicy="memory-disk"
              />
            ) : (
              <View style={[styles.avatar, styles.avatarEmpty]}>
                <Icon name="person-outline" size={theme.icon.xl} color={theme.color.brandText} />
              </View>
            )}
            <View style={styles.headerInfo}>
              <AppText variant="title" numberOfLines={1}>
                {person?.name ?? '人物'}
              </AppText>
              <AppText variant="caption" color="textTertiary" numberOfLines={1}>
                {platformLabel(person?.platform)} · {person?.inspiration_count ?? total} 张素材
                {person?.ip_location ? ` · ${person.ip_location}` : ''}
              </AppText>
            </View>
          </View>

          {person?.bio ? (
            <AppText variant="bodySm" color="textSecondary">
              {person.bio}
            </AppText>
          ) : null}

          <AppText variant="subtitle" style={styles.sectionTitle}>
            作品
          </AppText>
        </View>
      }
      renderItem={({ item }) => (
        <MediaCard
          uri={
            item.thumbnail_path
              ? getFileUrl(apiBaseUrl, item.thumbnail_path)
              : getFileUrl(apiBaseUrl, item.file_path)
          }
          video={item.media_type === 'video'}
          placeholderColor={dominantColor(item.dominant_colors) ?? undefined}
          onPress={() => router.push(`/detail/${item.id}`)}
        />
      )}
      ListEmptyComponent={
        error ? (
          <ErrorState onRetry={() => void loadWorks(1)} />
        ) : (
          <EmptyState icon="images-outline" title="这个人还没有作品" />
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
    header: { marginBottom: t.space.md, gap: t.space.md },
    headerTop: { flexDirection: 'row', alignItems: 'center', gap: t.space.md },
    avatar: {
      width: 56,
      height: 56,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.bgSubtle,
    },
    avatarEmpty: { alignItems: 'center', justifyContent: 'center', backgroundColor: t.color.brandSubtle },
    headerInfo: { flex: 1, gap: 2 },
    sectionTitle: { marginTop: t.space.sm },
    footer: { paddingVertical: t.space.lg },
  })
