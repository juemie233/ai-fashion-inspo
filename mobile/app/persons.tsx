/** 人物浏览：穿搭博主 / 模特两个 Tab，搜索 + 素材数排序 + 点击进作品墙。
 *
 * 手机侧取舍（docs/移动端.md）：只做浏览，不做新建/编辑/合并/人脸扫描 ——
 * 那些是维护工作，留在 Web。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Image,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import Icon from '../components/Icon'
import { fetchPersons, getFileUrl, type Person, type PersonKind } from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'

const PAGE_SIZE = 20

/** 平台中文名 */
const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音',
  xiaohongshu: '小红书',
  other: '其他',
  instagram: 'Instagram',
  weibo: '微博',
}

const TABS: { kind: PersonKind; label: string }[] = [
  { kind: 'blogger', label: '穿搭博主' },
  { kind: 'model', label: '模特' },
]

export default function PersonsScreen() {
  const router = useRouter()
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const [kind, setKind] = useState<PersonKind>('blogger')
  const [search, setSearch] = useState('')
  const [items, setItems] = useState<Person[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  /** 翻页互斥 + 请求序号：避免慢网重复触发 onEndReached 时追加重复页 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)

  const load = useCallback(
    async (nextPage = 1, nextKind = kind, keyword = search) => {
      if (nextPage > 1 && loadingMoreRef.current) return
      if (nextPage === 1) setLoading(true)
      const seq = ++requestSeqRef.current
      if (nextPage > 1) loadingMoreRef.current = true
      try {
        const data = await fetchPersons(nextKind, nextPage, PAGE_SIZE, keyword)
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
    [kind, search],
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

  return (
    <View style={styles.container}>
      {/* Tab 切换 */}
      <View style={styles.tabs}>
        {TABS.map((t) => {
          const active = t.kind === kind
          return (
            <TouchableOpacity
              key={t.kind}
              style={[styles.tab, active && styles.tabActive]}
              onPress={() => {
                if (t.kind === kind) return
                // 只改条件：load 的依赖变化会触发 effect 重新拉第一页（避免双请求）
                setKind(t.kind)
                setItems([])
                setPage(1)
              }}
            >
              <Text style={[styles.tabText, active && styles.tabTextActive]}>{t.label}</Text>
            </TouchableOpacity>
          )
        })}
      </View>

      {/* 搜索 */}
      <View style={styles.searchBar}>
        <Icon name="search" size={16} color="#9ca3af" />
        <TextInput
          style={styles.searchInput}
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={() => void load(1)}
          placeholder="搜人物名称"
          placeholderTextColor="#9ca3af"
          returnKeyType="search"
        />
        {search.length > 0 && (
          <TouchableOpacity
            onPress={() => {
              // 只改条件：load 的依赖变化会触发 effect 重新拉（避免双请求）
              setSearch('')
            }}
            hitSlop={8}
          >
            <Icon name="close-circle" size={16} color="#d1d5db" />
          </TouchableOpacity>
        )}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#6366f1" />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(p) => `${p.kind}-${p.id}`}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          onEndReached={onEndReached}
          onEndReachedThreshold={0.5}
          renderItem={({ item }) => (
            <TouchableOpacity
              testID={`person-${item.id}`}
              style={styles.row}
              onPress={() => router.push(`/person/${item.id}?kind=${item.kind}`)}
            >
              {item.avatar_path ? (
                <Image
                  source={{ uri: getFileUrl(apiBaseUrl, item.avatar_path) }}
                  style={styles.avatar}
                />
              ) : (
                <View style={[styles.avatar, styles.avatarEmpty]}>
                  <Icon name="person-outline" size={20} color="#c7d2fe" />
                </View>
              )}

              <View style={styles.info}>
                <View style={styles.nameRow}>
                  <Text style={styles.name} numberOfLines={1}>
                    {item.name}
                  </Text>
                  {item.face_registered ? (
                    <Icon name="checkmark-circle" size={12} color="#16a34a" />
                  ) : null}
                </View>
                <Text style={styles.meta} numberOfLines={1}>
                  {PLATFORM_LABELS[item.platform] || item.platform} · {item.inspiration_count} 张
                  {item.ip_location ? ` · ${item.ip_location}` : ''}
                </Text>
              </View>

              <Icon name="chevron-forward" size={16} color="#d1d5db" />
            </TouchableOpacity>
          )}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Icon name="people-outline" size={56} color="#d1d5db" />
              <Text style={styles.emptyText}>
                {error || (kind === 'blogger' ? '还没有穿搭博主' : '还没有模特')}
              </Text>
            </View>
          }
          ListFooterComponent={
            <Text style={styles.footer}>{total > 0 ? `共 ${total} 位` : ''}</Text>
          }
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  tabs: { flexDirection: 'row', backgroundColor: '#fff', paddingHorizontal: 12, paddingTop: 10 },
  tab: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 14,
    backgroundColor: '#f3f4f6',
    marginRight: 8,
  },
  tabActive: { backgroundColor: '#6366f1' },
  tabText: { fontSize: 13, color: '#374151' },
  tabTextActive: { color: '#fff', fontWeight: '500' },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#f3f4f6',
    marginHorizontal: 12,
    marginVertical: 10,
    paddingHorizontal: 10,
    height: 36,
    borderRadius: 8,
  },
  searchInput: { flex: 1, fontSize: 14, color: '#111827', padding: 0 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { paddingBottom: 24 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: '#fff',
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginHorizontal: 12,
    marginBottom: 8,
    borderRadius: 10,
  },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#f3f4f6' },
  avatarEmpty: { alignItems: 'center', justifyContent: 'center' },
  info: { flex: 1, gap: 2 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  name: { fontSize: 14, fontWeight: '500', color: '#111827' },
  meta: { fontSize: 11, color: '#9ca3af' },
  empty: { paddingTop: 80, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
  footer: { textAlign: 'center', fontSize: 11, color: '#d1d5db', paddingVertical: 12 },
})
