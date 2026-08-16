/** 首页画廊：瀑布流展示所有灵感素材，支持下拉刷新和无限滚动。 */

import { useState, useEffect, useCallback } from 'react'
import {
  View,
  Text,
  FlatList,
  Image,
  TouchableOpacity,
  StyleSheet,
  Dimensions,
  RefreshControl,
} from 'react-native'
import { useRouter } from 'expo-router'
import { Ionicons } from '@expo/vector-icons'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import { getFileUrl as buildFileUrl } from '../../services/api'
import { sourceLabel } from '../../utils/sourceLabel'

const { width } = Dimensions.get('window')
const CARD_WIDTH = (width - 36) / 2

export default function GalleryScreen() {
  const router = useRouter()
  const { items, loading, total, fetchInspirations, fetchMore, toggleFavorite } =
    useInspirationStore()

  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    fetchInspirations()
  }, [])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await fetchInspirations()
    setRefreshing(false)
  }, [])

  const onEndReached = () => {
    if (items.length < total && !loading) {
      fetchMore()
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
            ? getFileUrl(item.thumbnail_path)
            : getFileUrl(item.file_path),
        }}
        style={styles.cardImage}
      />      {/* 标签 */}
      <View style={styles.cardTags}>
        {item.tags?.slice(0, 3).map((t) => (
          <View key={t.tag.id} style={styles.tag}>
            <Text style={styles.tagText} numberOfLines={1}>
              {t.tag.name}
            </Text>
          </View>
        ))}
      </View>

      {/* 来源标识 */}
      <View style={styles.cardBadge}>
        <Text style={styles.cardBadgeText}>
          {sourceLabel(item.source_type)}
        </Text>
      </View>

      {/* 收藏按钮 */}
      <TouchableOpacity
        style={styles.favBtn}
        onPress={() => toggleFavorite(item.id, !item.is_favorite)}
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
      <FlatList
        data={items}
        renderItem={renderCard}
        keyExtractor={(item) => item.id}
        numColumns={2}
        contentContainerStyle={styles.grid}
        columnWrapperStyle={styles.row}
        onEndReached={onEndReached}
        onEndReachedThreshold={0.5}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Ionicons name="images-outline" size={64} color="#d1d5db" />
            <Text style={styles.emptyText}>还没有灵感素材</Text>
            <Text style={styles.emptySubtext}>去采集或上传一些穿搭图片吧</Text>
          </View>
        }
      />
    </View>
  )
}

/** 辅助函数：拼接文件地址（路径段 URL 编码，防止含空格/中文的路径 404） */
function getFileUrl(path: string) {
  const base = useInspirationStore.getState().apiBaseUrl
  return buildFileUrl(base, path)
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  grid: { padding: 12 },
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
  cardTags: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
    padding: 8,
  },
  tag: {
    backgroundColor: '#eef2ff',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  tagText: { fontSize: 10, color: '#6366f1' },
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
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingTop: 120,
  },
  emptyText: { fontSize: 16, color: '#9ca3af', marginTop: 12 },
  emptySubtext: { fontSize: 13, color: '#d1d5db', marginTop: 4 },
})
