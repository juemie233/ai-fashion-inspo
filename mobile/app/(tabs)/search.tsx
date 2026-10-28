/** 搜索页：关键词/标签筛选 + 分页 + 以图搜图。
 *
 * 手机侧取舍（docs/mobile-porting-plan.md）：
 * - 标签筛选不再是「全量 chip 墙」（库里有 2 万+ 标签，会直接卡死），改为弹层搜索多选
 * - 结果分页（onEndReached），不再一次性拉到底
 * - 以图搜图是手机端**独占优势**：相册选图 → `/search/vector`（CLIP 图像向量）
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Image,
  Alert,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import { useRouter } from 'expo-router'
import Icon from '../../components/Icon'
import TagFilterSheet from '../../components/TagFilterSheet'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import {
  apiClient,
  fetchTagGroups,
  getFileUrl,
  vectorSearchByImage,
  vectorSearchByText,
  type TagCategoryGroup,
  type VectorHit,
} from '../../services/api'
import { sourceLabel } from '../../utils/sourceLabel'

const PAGE_SIZE = 30

export default function SearchScreen() {
  const router = useRouter()
  const { apiBaseUrl, toggleFavorite } = useInspirationStore()

  const [searchText, setSearchText] = useState('')
  const [tagGroups, setTagGroups] = useState<TagCategoryGroup[]>([])
  const [tagsLoading, setTagsLoading] = useState(false)
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [tagSheetOpen, setTagSheetOpen] = useState(false)

  const [results, setResults] = useState<Inspiration[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  /** 当前结果是否来自以图搜图（用于标题文案与停止分页） */
  const [imageQueryUri, setImageQueryUri] = useState<string | null>(null)
  /** 检索模式：关键词（走 /search）或语义（走向量库 /search/vector 的 text 分支） */
  const [mode, setMode] = useState<'keyword' | 'semantic'>('keyword')
  /** 当前结果是否来自语义检索 */
  const [semanticQuery, setSemanticQuery] = useState<string | null>(null)
  /** 搜索请求序号：翻页在飞时重新搜索/以图搜图，旧响应直接丢弃，避免混入新结果 */
  const searchSeqRef = useRef(0)

  useEffect(() => {
    void loadTags()
  }, [])

  const loadTags = async () => {
    setTagsLoading(true)
    try {
      // 走会话级缓存：与标签管理/标签编辑器共享，避免 4.3MB/8 秒重复下载
      setTagGroups(await fetchTagGroups())
    } catch {
      // 标签拉取失败不阻塞搜索（可以只用关键词）
    } finally {
      setTagsLoading(false)
    }
  }

  /** 执行搜索（page=1 重置结果） */
  const doSearch = useCallback(
    async (nextPage = 1) => {
      const seq = ++searchSeqRef.current
      if (nextPage === 1) {
        setLoading(true)
        setLoadingMore(false)
        setSearched(true)
        setImageQueryUri(null)
        setSemanticQuery(null)
      } else {
        setLoadingMore(true)
      }
      setError('')

      // 语义检索：走向量库（text 分支），一次取 TopK，不分页
      if (mode === 'semantic' && nextPage === 1) {
        const text = searchText.trim()
        if (!text) {
          setError('语义检索需要先输入一句话，例如「白色连衣裙 约会」')
          setLoading(false)
          setLoadingMore(false)
          return
        }
        try {
          const hits: VectorHit[] = await vectorSearchByText(text, PAGE_SIZE)
          if (seq !== searchSeqRef.current) return
          setResults(hits.map((h) => h.inspiration).filter(Boolean))
          setTotal(hits.length)
          setSemanticQuery(text)
          setPage(1)
        } catch {
          if (seq !== searchSeqRef.current) return
          setError('语义检索失败：可能文本向量模型未就绪（需 Ollama + 嵌入模型）')
          setResults([])
          setTotal(0)
        } finally {
          if (seq === searchSeqRef.current) {
            setLoading(false)
            setLoadingMore(false)
          }
        }
        return
      }

      try {
        const params: Record<string, string | number> = { page: nextPage, size: PAGE_SIZE }
        const tags = [...selectedTags]
        const keyword = searchText.trim()
        if (tags.length > 0) params.include_tags = tags.join(',')
        if (keyword) params.keyword = keyword

        const { data } = await apiClient.get<{ items: Inspiration[]; total: number }>('/search', {
          params,
        })
        // 已发起更新的搜索：这次响应作废，否则旧第 2 页会拼进新结果
        if (seq !== searchSeqRef.current) return
        const items = data?.items ?? []
        setResults((prev) => (nextPage === 1 ? items : [...prev, ...items]))
        setTotal(data?.total ?? 0)
        setPage(nextPage)
      } catch {
        if (seq !== searchSeqRef.current) return
        setError('搜索失败，请确认后端服务已启动')
        if (nextPage === 1) {
          setResults([])
          setTotal(0)
        }
      } finally {
        if (seq === searchSeqRef.current) {
          setLoading(false)
          setLoadingMore(false)
        }
      }
    },
    [searchText, selectedTags, mode],
  )

  /** 以图搜图：相册选图 → 图像向量检索 */
  const doImageSearch = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在系统设置中允许访问相册')
      return
    }
    const picked = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.8,
      allowsMultipleSelection: false,
    })
    if (picked.canceled || !picked.assets?.[0]) return

    const uri = picked.assets[0].uri
    const seq = ++searchSeqRef.current
    setLoading(true)
    setLoadingMore(false)
    setSearched(true)
    setError('')
    try {
      const hits: VectorHit[] = await vectorSearchByImage(uri, PAGE_SIZE)
      if (seq !== searchSeqRef.current) return
      setResults(hits.map((h) => h.inspiration).filter(Boolean))
      setTotal(hits.length)
      setImageQueryUri(uri)
      setPage(1)
    } catch {
      if (seq !== searchSeqRef.current) return
      setError('以图搜图失败：可能图像向量模型未就绪')
      setResults([])
      setTotal(0)
    } finally {
      if (seq === searchSeqRef.current) setLoading(false)
    }
  }

  const onEndReached = () => {
    // 以图搜图 / 语义检索的结果都是一次性返回的，不做分页
    if (imageQueryUri || semanticQuery) return
    if (!loadingMore && !loading && results.length < total) void doSearch(page + 1)
  }

  /** 切换收藏：结果项本地更新（结果不经过素材库 store 列表） */
  const handleToggleFavorite = async (item: Inspiration) => {
    const newState = !item.is_favorite
    try {
      await toggleFavorite(item.id, newState)
      setResults((prev) =>
        prev.map((r) => (r.id === item.id ? { ...r, is_favorite: newState } : r)),
      )
    } catch {
      setError('收藏操作失败')
    }
  }

  const renderCard = ({ item }: { item: Inspiration }) => (
    <TouchableOpacity
      style={styles.card}
      activeOpacity={0.8}
      onPress={() => router.push(`/detail/${item.id}`)}
    >
      <Image
        source={{
          uri: item.thumbnail_path
            ? getFileUrl(apiBaseUrl, item.thumbnail_path)
            : getFileUrl(apiBaseUrl, item.file_path),
        }}
        style={styles.cardImage}
      />
      <View style={styles.cardBadge}>
        <Text style={styles.cardBadgeText}>{sourceLabel(item.source_type)}</Text>
      </View>
      <TouchableOpacity style={styles.favBtn} onPress={() => handleToggleFavorite(item)}>
        <Icon
          name={item.is_favorite ? 'heart' : 'heart-outline'}
          size={18}
          color={item.is_favorite ? '#ef4444' : '#fff'}
        />
      </TouchableOpacity>
    </TouchableOpacity>
  )

  return (
    <View style={styles.container}>
      {/* 搜索栏 */}
      <View style={styles.searchBar}>
        <Icon name="search" size={18} color="#9ca3af" />
        <TextInput
          style={styles.searchInput}
          placeholder="搜标签名 / 作者 / 文件名"
          placeholderTextColor="#9ca3af"
          value={searchText}
          onChangeText={setSearchText}
          onSubmitEditing={() => void doSearch(1)}
          returnKeyType="search"
        />
        {searchText.length > 0 && (
          <TouchableOpacity onPress={() => setSearchText('')} hitSlop={8}>
            <Icon name="close-circle" size={18} color="#9ca3af" />
          </TouchableOpacity>
        )}
      </View>

      {/* 检索模式：关键词（标签/全文）或语义（向量） */}
      <View style={styles.modeRow}>
        {([
          { key: 'keyword', label: '关键词' },
          { key: 'semantic', label: '语义' },
        ] as const).map((m) => {
          const active = mode === m.key
          return (
            <TouchableOpacity
              key={m.key}
              testID={`mode-${m.key}`}
              style={[styles.modeChip, active && styles.modeChipActive]}
              onPress={() => setMode(m.key)}
            >
              <Text style={[styles.modeText, active && styles.modeTextActive]}>{m.label}</Text>
            </TouchableOpacity>
          )
        })}
        {mode === 'semantic' && (
          <Text style={styles.modeHint}>用一句话描述想找的穿搭</Text>
        )}
      </View>

      {/* 条件行：标签筛选 + 以图搜图 + 搜索 */}
      <View style={styles.condRow}>
        {mode === 'keyword' && (
        <TouchableOpacity style={styles.condBtn} onPress={() => setTagSheetOpen(true)}>
          <Icon name="pricetags-outline" size={16} color="#6366f1" />
          <Text style={styles.condText}>
            标签{selectedTags.length > 0 ? ` (${selectedTags.length})` : ''}
          </Text>
        </TouchableOpacity>
        )}

        {mode === 'keyword' && (
          <TouchableOpacity style={styles.condBtn} onPress={() => void doImageSearch()}>
            <Icon name="images-outline" size={16} color="#6366f1" />
            <Text style={styles.condText}>以图搜图</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity style={styles.searchBtn} onPress={() => void doSearch(1)}>
          <Text style={styles.searchBtnText}>搜索</Text>
        </TouchableOpacity>
      </View>

      {/* 已选标签 */}
      {selectedTags.length > 0 && (
        <View style={styles.selectedRow}>
          {selectedTags.map((name) => (
            <TouchableOpacity
              key={name}
              style={styles.selectedChip}
              onPress={() => setSelectedTags((prev) => prev.filter((n) => n !== name))}
            >
              <Text style={styles.selectedChipText}>{name}</Text>
              <Icon name="close" size={11} color="#6366f1" />
            </TouchableOpacity>
          ))}
        </View>
      )}

      {/* 以图搜图预览 */}
      {imageQueryUri && (
        <View style={styles.queryRow}>
          <Image source={{ uri: imageQueryUri }} style={styles.queryThumb} />
          <Text style={styles.queryText}>以这张图搜到的相似素材</Text>
        </View>
      )}

      {loading && <ActivityIndicator style={{ marginTop: 16 }} color="#6366f1" />}
      {error !== '' && <Text style={styles.errorText}>{error}</Text>}
      {!loading && searched && total === 0 && error === '' && (
        <Text style={styles.resultCount}>没有找到匹配的素材</Text>
      )}

      {results.length > 0 && (
        <FlatList
          data={results}
          renderItem={renderCard}
          keyExtractor={(item) => item.id}
          numColumns={2}
          columnWrapperStyle={styles.row}
          contentContainerStyle={styles.resultsGrid}
          onEndReached={onEndReached}
          onEndReachedThreshold={0.5}
          ListHeaderComponent={
            <Text style={styles.resultCount}>
              {semanticQuery ? `语义匹配 ${total} 条` : `找到 ${total} 条结果`}
            </Text>
          }
          ListFooterComponent={
            loadingMore ? <ActivityIndicator style={{ marginVertical: 12 }} color="#6366f1" /> : null
          }
        />
      )}

      <TagFilterSheet
        visible={tagSheetOpen}
        loading={tagsLoading}
        groups={tagGroups}
        selected={selectedTags}
        onClose={() => setTagSheetOpen(false)}
        onConfirm={(names) => {
          setSelectedTags(names)
          setTagSheetOpen(false)
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    marginHorizontal: 12,
    marginTop: 12,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    height: 44,
  },
  searchInput: { flex: 1, marginLeft: 8, fontSize: 15, color: '#1f2937' },
  modeRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingTop: 10 },
  modeChip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, backgroundColor: '#f3f4f6' },
  modeChipActive: { backgroundColor: '#6366f1' },
  modeText: { fontSize: 12, color: '#374151' },
  modeTextActive: { color: '#fff', fontWeight: '500' },
  modeHint: { fontSize: 11, color: '#9ca3af' },
  condRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  condBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 8,
    backgroundColor: '#eef2ff',
  },
  condText: { fontSize: 12, color: '#6366f1' },
  searchBtn: {
    flex: 1,
    backgroundColor: '#6366f1',
    paddingVertical: 9,
    borderRadius: 8,
    alignItems: 'center',
  },
  searchBtnText: { color: '#fff', fontSize: 14, fontWeight: '600' },
  selectedRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    paddingHorizontal: 12,
    paddingBottom: 8,
  },
  selectedChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: '#eef2ff',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  selectedChipText: { fontSize: 12, color: '#6366f1' },
  queryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingBottom: 8,
  },
  queryThumb: { width: 40, height: 40, borderRadius: 6, backgroundColor: '#e5e7eb' },
  queryText: { fontSize: 12, color: '#6b7280' },
  resultCount: { paddingHorizontal: 12, paddingVertical: 8, fontSize: 13, color: '#6b7280' },
  errorText: { paddingHorizontal: 12, paddingVertical: 8, fontSize: 13, color: '#ef4444' },
  resultsGrid: { paddingHorizontal: 12, paddingBottom: 24 },
  row: { gap: 12, marginBottom: 12 },
  card: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
  },
  cardImage: { width: '100%', aspectRatio: 2 / 3, backgroundColor: '#f3f4f6' },
  cardBadge: {
    position: 'absolute',
    top: 8,
    left: 8,
    backgroundColor: 'rgba(0,0,0,0.5)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  cardBadgeText: { fontSize: 10, color: '#fff' },
  favBtn: {
    position: 'absolute',
    top: 8,
    right: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(0,0,0,0.3)',
    alignItems: 'center',
    justifyContent: 'center',
  },
})
