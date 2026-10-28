/** 合集宫格页：所有收藏夹（一级手动合集）的封面 + 名称 + 数量。
 *
 * 手机侧取舍：不做拖拽排序、不做智能合集的规则编辑（见 docs/mobile-porting-plan.md）。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Image,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import Icon from '../components/Icon'
import { fetchCollections, getFileUrl, type CollectionItem } from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'

export default function CollectionsScreen() {
  const router = useRouter()
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
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#6366f1" />
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
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      renderItem={({ item }) => (
        <TouchableOpacity
          style={styles.card}
          activeOpacity={0.85}
          onPress={() => router.push(`/collection/${item.id}`)}
        >
          {item.cover_thumbnail_path ? (
            <Image
              source={{ uri: getFileUrl(apiBaseUrl, item.cover_thumbnail_path) }}
              style={styles.cover}
            />
          ) : (
            <View style={[styles.cover, styles.coverEmpty]}>
              <Icon name="albums-outline" size={28} color="#c7d2fe" />
            </View>
          )}
          <View style={styles.meta}>
            <Text style={styles.name} numberOfLines={1}>
              {item.name}
            </Text>
            <Text style={styles.count}>
              {item.kind === 'smart' ? '智能合集' : `${item.item_count ?? 0} 张`}
            </Text>
          </View>
        </TouchableOpacity>
      )}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Icon name="albums-outline" size={56} color="#d1d5db" />
          <Text style={styles.emptyText}>{error || '还没有合集'}</Text>
          <Text style={styles.emptyHint}>在素材库长按素材 → 加合集，可现场新建</Text>
        </View>
      }
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#f9fafb' },
  grid: { padding: 12, paddingBottom: 24 },
  row: { gap: 12, marginBottom: 12 },
  card: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
  },
  cover: { width: '100%', aspectRatio: 1, backgroundColor: '#f3f4f6' },
  coverEmpty: { alignItems: 'center', justifyContent: 'center' },
  meta: { padding: 10, gap: 2 },
  name: { fontSize: 14, fontWeight: '500', color: '#111827' },
  count: { fontSize: 11, color: '#9ca3af' },
  empty: { paddingTop: 80, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
  emptyHint: { fontSize: 12, color: '#d1d5db' },
})
