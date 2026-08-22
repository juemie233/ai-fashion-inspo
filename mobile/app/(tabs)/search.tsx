/** 搜索页：标签筛选 + 关键词搜索 + 结果网格。 */

import { useState, useEffect } from 'react'
import {
  View,
  Text,
  TextInput,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  Image,
  Dimensions,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useRouter } from 'expo-router'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import { apiClient, getFileUrl, type TagCategoryGroup } from '../../services/api'
import { sourceLabel } from '../../utils/sourceLabel'

const { width } = Dimensions.get('window')
const CARD_WIDTH = (width - 36) / 2

const CAT_LABELS: Record<string, string> = {
  style: '风格', item_type: '单品', color: '颜色',
  body_part: '穿着方式', fit: '版型',
  season: '季节', attribute: '属性', free: '自定义',
}

export default function SearchScreen() {
  const router = useRouter()
  const { apiBaseUrl, toggleFavorite } = useInspirationStore()
  const [searchText, setSearchText] = useState('')
  const [tagGroups, setTagGroups] = useState<TagCategoryGroup[]>([])
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())
  const [results, setResults] = useState<Inspiration[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    loadTags()
  }, [])

  const loadTags = async () => {
    try {
      const { data } = await apiClient.get('/tags')
      setTagGroups(data)
    } catch (e) {
      console.error('加载标签失败', e)
    }
  }

  const toggleTag = (name: string) => {
    const next = new Set(selectedTags)
    if (next.has(name)) {
      next.delete(name)
    } else {
      next.add(name)
    }
    setSelectedTags(next)
  }

  const doSearch = async () => {
    setLoading(true)
    setError('')
    setSearched(true)
    try {
      const params: Record<string, string> = {}
      if (selectedTags.size > 0) {
        params.include_tags = Array.from(selectedTags).join(',')
      }
      if (searchText.trim()) {
        // 追加手动输入的标签
        const existing = params.include_tags || ''
        params.include_tags = existing ? `${existing},${searchText.trim()}` : searchText.trim()
      }

      const { data } = await apiClient.get('/search', { params })
      setResults(data.items)
      setTotal(data.total)
    } catch {
      setError('搜索失败，请确认后端服务已启动')
      setResults([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }

  /** 切换收藏：结果项本地更新（结果不经过素材库 store 列表） */
  const handleToggleFavorite = async (item: Inspiration) => {
    try {
      const newState = !item.is_favorite
      await toggleFavorite(item.id, newState)
      setResults((prev) =>
        prev.map((r) => (r.id === item.id ? { ...r, is_favorite: newState } : r))
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
      {/* 来源标识 */}
      <View style={styles.cardBadge}>
        <Text style={styles.cardBadgeText}>{sourceLabel(item.source_type)}</Text>
      </View>
      {/* 收藏按钮 */}
      <TouchableOpacity
        style={styles.favBtn}
        onPress={() => handleToggleFavorite(item)}
      >
        <Ionicons
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
        <Ionicons name="search" size={18} color="#9ca3af" />
        <TextInput
          style={styles.searchInput}
          placeholder="搜索标签，逗号分隔 (如: JK制服, 白色)"
          placeholderTextColor="#9ca3af"
          value={searchText}
          onChangeText={setSearchText}
          onSubmitEditing={doSearch}
          returnKeyType="search"
        />
        {searchText.length > 0 && (
          <TouchableOpacity onPress={() => setSearchText('')}>
            <Ionicons name="close-circle" size={18} color="#9ca3af" />
          </TouchableOpacity>
        )}
      </View>

      {/* 标签筛选（限高滚动，为结果区留出空间） */}
      <ScrollView style={styles.tagsPanel} showsVerticalScrollIndicator={false}>
        {tagGroups.map((group) => (
          <View key={group.category} style={styles.tagGroup}>
            <Text style={styles.tagCategoryTitle}>
              {CAT_LABELS[group.category] || group.category}
            </Text>
            <View style={styles.tagChips}>
              {group.tags.map((tag) => (
                <TouchableOpacity
                  key={tag.id}
                  style={[
                    styles.tagChip,
                    selectedTags.has(tag.name) && styles.tagChipSelected,
                  ]}
                  onPress={() => toggleTag(tag.name)}
                >
                  <Text
                    style={[
                      styles.tagChipText,
                      selectedTags.has(tag.name) && styles.tagChipTextSelected,
                    ]}
                  >
                    {tag.name}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ))}
      </ScrollView>

      {/* 搜索按钮 */}
      <TouchableOpacity style={styles.searchBtn} onPress={doSearch}>
        <Text style={styles.searchBtnText}>
          搜索 {selectedTags.size > 0 ? `(${selectedTags.size} 个标签)` : ''}
        </Text>
      </TouchableOpacity>

      {/* 结果 */}
      {loading && <ActivityIndicator style={{ marginTop: 16 }} color="#6366f1" />}
      {error !== '' && (
        <Text style={styles.errorText}>{error}</Text>
      )}
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
          ListHeaderComponent={
            <Text style={styles.resultCount}>找到 {total} 条结果</Text>
          }
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    margin: 12,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    height: 44,
  },
  searchInput: {
    flex: 1,
    marginLeft: 8,
    fontSize: 15,
    color: '#1f2937',
  },
  tagsPanel: { maxHeight: 200, paddingHorizontal: 12 },
  tagGroup: { marginBottom: 16 },
  tagCategoryTitle: {
    fontSize: 13,
    color: '#6b7280',
    marginBottom: 8,
    fontWeight: '500',
  },
  tagChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tagChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: '#f3f4f6',
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  tagChipSelected: {
    backgroundColor: '#eef2ff',
    borderColor: '#6366f1',
  },
  tagChipText: { fontSize: 13, color: '#4b5563' },
  tagChipTextSelected: { color: '#6366f1', fontWeight: '500' },
  searchBtn: {
    margin: 12,
    backgroundColor: '#6366f1',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  searchBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  resultCount: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 13,
    color: '#6b7280',
  },
  errorText: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 13,
    color: '#ef4444',
  },
  resultsGrid: { paddingHorizontal: 12, paddingBottom: 24 },
  row: { gap: 12, marginBottom: 12 },
  card: {
    width: CARD_WIDTH,
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 2,
  },
  cardImage: {
    width: '100%',
    aspectRatio: 2 / 3,
    backgroundColor: '#f3f4f6',
  },
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
