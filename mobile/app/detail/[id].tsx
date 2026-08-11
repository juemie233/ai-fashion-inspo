/** 素材详情页：大图浏览 + 标签展示。 */

import { useState, useEffect } from 'react'
import {
  View,
  Text,
  Image,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Dimensions,
} from 'react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { Ionicons } from '@expo/vector-icons'
import { apiClient } from '../../services/api'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'

const { width } = Dimensions.get('window')

export default function DetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const router = useRouter()
  const { apiBaseUrl, toggleFavorite } = useInspirationStore()

  const [detail, setDetail] = useState<Inspiration | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadDetail()
  }, [id])

  const loadDetail = async () => {
    try {
      const { data } = await apiClient.get(`/inspirations/${id}`)
      setDetail(data)
    } catch {
    } finally {
      setLoading(false)
    }
  }

  const getFileUrl = (path: string) => `${apiBaseUrl}/api/files/${path}`

  const handleToggleFav = async () => {
    if (!detail) return
    const newState = !detail.is_favorite
    await toggleFavorite(id!)
    setDetail({ ...detail, is_favorite: newState })
  }

  if (loading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" color="#6366f1" />
      </View>
    )
  }

  if (!detail) {
    return (
      <View style={styles.loading}>
        <Text style={{ color: '#9ca3af' }}>素材未找到</Text>
      </View>
    )
  }

  // 按类别分组标签
  const groupedTags: Record<string, any[]> = {}
  detail.tags?.forEach((t) => {
    const cat = t.tag.category
    if (!groupedTags[cat]) groupedTags[cat] = []
    groupedTags[cat].push(t)
  })

  const CAT_LABELS: Record<string, string> = {
    style: '风格', item_type: '单品', color: '颜色',
    body_part: '穿着方式', fit: '版型', occasion: '场合',
    season: '季节', attribute: '属性', free: '自定义',
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 40 }}>
      {/* 大图 */}
      <Image
        source={{ uri: getFileUrl(detail.file_path) }}
        style={styles.mainImage}
      />

      {/* 操作栏 */}
      <View style={styles.actions}>
        <TouchableOpacity style={styles.favBtn} onPress={handleToggleFav}>
          <Ionicons
            name={detail.is_favorite ? 'heart' : 'heart-outline'}
            size={24}
            color={detail.is_favorite ? '#ef4444' : '#6b7280'}
          />
          <Text style={styles.favText}>
            {detail.is_favorite ? '已收藏' : '收藏'}
          </Text>
        </TouchableOpacity>

        {detail.source_url && (
          <TouchableOpacity style={styles.linkBtn}>
            <Ionicons name="link-outline" size={20} color="#6366f1" />
            <Text style={styles.linkText}>查看原链接</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* 元信息 */}
      <View style={styles.metaSection}>
        <View style={styles.metaRow}>
          <Text style={styles.metaLabel}>来源</Text>
          <Text style={styles.metaValue}>{detail.source_type}</Text>
        </View>
        {detail.source_author && (
          <View style={styles.metaRow}>
            <Text style={styles.metaLabel}>作者</Text>
            <Text style={styles.metaValue}>@{detail.source_author}</Text>
          </View>
        )}
        <View style={styles.metaRow}>
          <Text style={styles.metaLabel}>时间</Text>
          <Text style={styles.metaValue}>
            {new Date(detail.created_at).toLocaleDateString('zh-CN')}
          </Text>
        </View>
      </View>

      {/* 标签 */}
      {detail.tags && detail.tags.length > 0 ? (
        <View style={styles.tagsSection}>
          <Text style={styles.sectionTitle}>标签</Text>
          {Object.entries(groupedTags).map(([category, tags]) => (
            <View key={category} style={styles.tagGroup}>
              <Text style={styles.tagCategoryLabel}>
                {CAT_LABELS[category] || category}
              </Text>
              <View style={styles.tagChips}>
                {tags.map((t) => (
                  <View key={t.tag.id} style={styles.tagChip}>
                    <Text style={styles.tagChipText}>
                      {t.tag.name}
                      {t.confidence < 0.8
                        ? ` (${Math.round(t.confidence * 100)}%)`
                        : ''}
                    </Text>
                  </View>
                ))}
              </View>
            </View>
          ))}
        </View>
      ) : (
        <View style={styles.noTags}>
          <Ionicons name="pricetags-outline" size={32} color="#d1d5db" />
          <Text style={styles.noTagsText}>暂无标签，AI 分析后自动生成</Text>
        </View>
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  mainImage: {
    width: width,
    height: width * 1.25,
    backgroundColor: '#f3f4f6',
    resizeMode: 'contain',
  },
  actions: {
    flexDirection: 'row',
    padding: 16,
    gap: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  favBtn: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  favText: { fontSize: 14, color: '#6b7280' },
  linkBtn: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  linkText: { fontSize: 14, color: '#6366f1' },
  metaSection: {
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  metaRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  metaLabel: { fontSize: 14, color: '#9ca3af' },
  metaValue: { fontSize: 14, color: '#1f2937' },
  tagsSection: { padding: 16 },
  sectionTitle: { fontSize: 16, fontWeight: '600', marginBottom: 12, color: '#1f2937' },
  tagGroup: { marginBottom: 14 },
  tagCategoryLabel: {
    fontSize: 12,
    color: '#9ca3af',
    marginBottom: 6,
  },
  tagChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tagChip: {
    backgroundColor: '#eef2ff',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  tagChipText: { fontSize: 13, color: '#6366f1' },
  noTags: {
    alignItems: 'center',
    padding: 40,
    gap: 8,
  },
  noTagsText: { fontSize: 14, color: '#9ca3af' },
})
