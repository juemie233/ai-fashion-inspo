/** 合集宫格页：所有收藏夹（一级手动合集）的封面 + 名称 + 数量。
 *
 * 手机侧取舍：不做拖拽排序、不做智能合集的规则编辑（见 docs/移动端.md）。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、封面 1:1 圆角 14、「智能合集」用 Badge、
 * 下拉刷新颜色跟主题。
 */

import { useCallback, useEffect, useState } from 'react'
import { FlatList, Pressable, RefreshControl, StyleSheet, View } from 'react-native'
import { Image } from 'expo-image'
import { useRouter } from 'expo-router'
import Icon from '../components/Icon'
import AppText from '../components/ui/AppText'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonGrid } from '../components/ui/Skeleton'
import { fetchCollections, getFileUrl, type CollectionItem } from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'

export default function CollectionsScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const [collections, setCollections] = useState<CollectionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const list = await fetchCollections()
      // 只展示一级合集；二级收藏夹在合集详情里体现
      setCollections(list.filter((c) => !c.parent_id))
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const onRefresh = () => {
    setRefreshing(true)
    void load()
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonGrid count={4} columns={2} ratio={1} />
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={collections}
      keyExtractor={(c) => String(c.id)}
      numColumns={2}
      columnWrapperStyle={styles.row}
      contentContainerStyle={styles.grid}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          colors={[theme.color.brandFill]}
          tintColor={theme.color.brandFill}
        />
      }
      renderItem={({ item }) => (
        <Pressable
          style={({ pressed }) => [styles.card, pressed && styles.pressed]}
          onPress={() => router.push(`/collection/${item.id}`)}
          accessibilityRole="button"
        >
          {item.cover_thumbnail_path ? (
            <Image
              source={{ uri: getFileUrl(apiBaseUrl, item.cover_thumbnail_path) }}
              style={styles.cover}
              contentFit="cover"
              transition={150}
              cachePolicy="memory-disk"
            />
          ) : (
            <View style={[styles.cover, styles.coverEmpty]}>
              <Icon name="albums-outline" size={theme.icon.xl} color={theme.color.textDisabled} />
            </View>
          )}
          <View style={styles.meta}>
            <AppText variant="subtitle" numberOfLines={1}>
              {item.name}
            </AppText>
            {item.kind === 'smart' ? (
              <Badge text="智能合集" tone="brand" />
            ) : (
              <AppText variant="caption" color="textTertiary" tabularNums>
                {item.item_count ?? 0} 张
              </AppText>
            )}
          </View>
        </Pressable>
      )}
      ListEmptyComponent={
        error ? (
          <ErrorState onRetry={() => void load()} />
        ) : (
          <EmptyState
            icon="albums-outline"
            title="还没有合集"
            description="在素材库长按素材 → 加合集，可现场新建"
          />
        )
      }
    />
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    row: { gap: t.space.md, marginBottom: t.space.md },
    card: {
      flex: 1,
      maxWidth: '48%',
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      overflow: 'hidden',
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
    },
    pressed: { opacity: 0.85 },
    cover: { width: '100%', aspectRatio: 1, backgroundColor: t.color.bgSubtle },
    coverEmpty: { alignItems: 'center', justifyContent: 'center' },
    meta: { padding: t.space.md, gap: t.space.xs },
  })
