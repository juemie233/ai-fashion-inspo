/** 搜索页：标签筛选 + 关键词搜索。 */

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
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useRouter } from 'expo-router'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import { apiClient, type TagCategoryGroup } from '../../services/api'

export default function SearchScreen() {
  const router = useRouter()
  const [searchText, setSearchText] = useState('')
  const [tagGroups, setTagGroups] = useState<TagCategoryGroup[]>([])
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())
  const [results, setResults] = useState<Inspiration[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    loadTags()
  }, [])

  const loadTags = async () => {
    try {
      const { data } = await apiClient.get('/tags')
      setTagGroups(data)
    } catch {}
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
    } finally {
      setLoading(false)
    }
  }

  const CAT_LABELS: Record<string, string> = {
    style: '风格', item_type: '单品', color: '颜色',
    body_part: '穿着方式', fit: '版型', occasion: '场合',
    season: '季节', attribute: '属性', free: '自定义',
  }

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

      {/* 标签筛选 */}
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
      {total > 0 && (
        <Text style={styles.resultCount}>找到 {total} 条结果</Text>
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
  tagsPanel: { flex: 1, paddingHorizontal: 12 },
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
    paddingBottom: 8,
    fontSize: 13,
    color: '#6b7280',
  },
})
