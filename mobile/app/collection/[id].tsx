/** 合集详情：合集内素材的两列网格（分页加载）。 */

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
import { useLocalSearchParams, useRouter } from 'expo-router'
import Icon from '../../components/Icon'
import {
  fetchCollectionInspirations,
  getFileUrl,
  type Inspiration,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'

const PAGE_SIZE = 30

export default function CollectionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const router = useRouter()
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const [items, setItems] = useState<Inspiration[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  const collectionId = Number(id)

  const load = useCallback(
    async (nextPage = 1) => {
      if (!Number.isFinite(collectionId)) {
        setError('合集不存在')
        setLoading(false)
        return
      }
      try {
        const data = await fetchCollectionInspirations(collectionId, nextPage, PAGE_SIZE)
        setItems((prev) => (nextPage === 1 ? data.items : [...prev, ...data.items]))
        setTotal(data.total)
        setPage(nextPage)
        setError('')
      } catch {
        setError('加载失败，请检查网络或后端服务')
      } finally {
        setLoading(false)
        setRefreshing(false)
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
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#6366f1" />
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
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      renderItem={({ item }) => (
        <TouchableOpacity
          style={styles.card}
          activeOpacity={0.85}
          onPress={() => router.push(`/detail/${item.id}`)}
        >
          <Image
            source={{
              uri: item.thumbnail_path
                ? getFileUrl(apiBaseUrl, item.thumbnail_path)
                : getFileUrl(apiBaseUrl, item.file_path),
            }}
            style={styles.image}
          />
        </TouchableOpacity>
      )}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Icon name="images-outline" size={56} color="#d1d5db" />
          <Text style={styles.emptyText}>{error || '合集里还没有素材'}</Text>
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
  card: { flex: 1, aspectRatio: 0.75, borderRadius: 10, overflow: 'hidden', backgroundColor: '#fff' },
  image: { width: '100%', height: '100%', backgroundColor: '#f3f4f6' },
  empty: { paddingTop: 80, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
})
