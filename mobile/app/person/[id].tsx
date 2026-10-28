/** 人物作品墙：头部资料 + 该人物素材的分页网格。 */

import { useCallback, useEffect, useRef, useState } from 'react'
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
  fetchPerson,
  fetchPersonInspirations,
  getFileUrl,
  type Inspiration,
  type Person,
  type PersonKind,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'

const PAGE_SIZE = 30

const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音',
  xiaohongshu: '小红书',
  other: '其他',
  instagram: 'Instagram',
  weibo: '微博',
}

export default function PersonDetailScreen() {
  const { id, kind } = useLocalSearchParams<{ id: string; kind?: string }>()
  const router = useRouter()
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const personKind: PersonKind = kind === 'model' ? 'model' : 'blogger'
  const personId = Number(id)

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
      ListHeaderComponent={
        <View style={styles.header}>
          <View style={styles.headerTop}>
            {person?.avatar_path ? (
              <Image
                source={{ uri: getFileUrl(apiBaseUrl, person.avatar_path) }}
                style={styles.avatar}
              />
            ) : (
              <View style={[styles.avatar, styles.avatarEmpty]}>
                <Icon name="person-outline" size={26} color="#c7d2fe" />
              </View>
            )}
            <View style={styles.headerInfo}>
              <Text style={styles.name}>{person?.name ?? '人物'}</Text>
              <Text style={styles.meta}>
                {PLATFORM_LABELS[person?.platform ?? 'other'] ?? person?.platform} ·{' '}
                {person?.inspiration_count ?? total} 张素材
                {person?.ip_location ? ` · ${person.ip_location}` : ''}
              </Text>
            </View>
          </View>

          {person?.bio ? <Text style={styles.bio}>{person.bio}</Text> : null}

          <Text style={styles.sectionTitle}>作品</Text>
        </View>
      }
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
          <Icon name="images-outline" size={48} color="#d1d5db" />
          <Text style={styles.emptyText}>{error || '这个人还没有作品'}</Text>
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
  header: { marginBottom: 8 },
  headerTop: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: '#fff' },
  avatarEmpty: { alignItems: 'center', justifyContent: 'center', backgroundColor: '#eef2ff' },
  headerInfo: { flex: 1, gap: 3 },
  name: { fontSize: 17, fontWeight: '600', color: '#111827' },
  meta: { fontSize: 12, color: '#9ca3af' },
  bio: { fontSize: 13, color: '#4b5563', marginTop: 10, lineHeight: 19 },
  sectionTitle: { fontSize: 14, fontWeight: '600', color: '#374151', marginTop: 14 },
  card: { flex: 1, aspectRatio: 0.75, borderRadius: 10, overflow: 'hidden', backgroundColor: '#fff' },
  image: { width: '100%', height: '100%', backgroundColor: '#f3f4f6' },
  empty: { paddingTop: 40, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 13, color: '#9ca3af' },
})
