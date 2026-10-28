/** 素材库：两列网格 + 筛选/排序 + 长按多选（批量收藏 / 加合集 / 移入垃圾桶）。 */

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
  Alert,
} from 'react-native'
import { useRouter } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Icon from '../../components/Icon'
import FilterBar from '../../components/FilterBar'
import SelectionBar from '../../components/SelectionBar'
import CollectionPicker from '../../components/CollectionPicker'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import {
  addToCollection,
  createCollection,
  fetchCollections,
  getFileUrl,
  type CollectionItem,
} from '../../services/api'
import { sourceLabel } from '../../utils/sourceLabel'

const { width } = Dimensions.get('window')

/** 批量收藏/垃圾桶的单次上限（与后端 Pydantic 校验保持一致，避免 422 只报通用失败） */
const MAX_BATCH = 500
/** 合集单次加入上限（后端 AddInspirationsRequest 为 200） */
const MAX_COLLECTION_BATCH = 200

/** 超过后端上限时先给出明确提示，返回 false 表示调用方应中止本次操作 */
function ensureBatchLimit(count: number, limit: number, action: string): boolean {
  if (count <= limit) return true
  Alert.alert('数量过多', `单次${action}最多 ${limit} 张，请减少选择后再试`)
  return false
}

export default function GalleryScreen() {
  const router = useRouter()
  const {
    items,
    loading,
    total,
    filter,
    sort,
    apiBaseUrl,
    fetchInspirations,
    fetchMore,
    toggleFavorite,
    setFilter,
    setSort,
    batchFavorite,
    batchTrash,
  } = useInspirationStore()

  // 首页隐藏了导航栏 header，顶栏要自己避开状态栏/刘海
  const insets = useSafeAreaInsets()
  const [refreshing, setRefreshing] = useState(false)
  /** 多选态：非空即处于多选模式（长按卡片进入） */
  const [selected, setSelected] = useState<string[]>([])
  /** 批量操作进行中 */
  const [busy, setBusy] = useState(false)

  // 合集选择器
  const [pickerOpen, setPickerOpen] = useState(false)
  const [collections, setCollections] = useState<CollectionItem[]>([])
  const [collectionsLoading, setCollectionsLoading] = useState(false)

  useEffect(() => {
    fetchInspirations()
  }, [])

  // 筛选/排序变化后列表已被替换：多选必须一起清掉，否则批量操作会落到看不见的旧素材上
  useEffect(() => {
    setSelected([])
  }, [filter, sort])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    setSelected([])
    await fetchInspirations()
    setRefreshing(false)
  }, [])

  const onEndReached = () => {
    if (items.length < total && !loading) {
      fetchMore()
    }
  }

  const selectionMode = selected.length > 0
  const selectedItems = items.filter((i) => selected.includes(i.id))
  const favoriteCount = selectedItems.filter((i) => i.is_favorite).length

  const toggleSelect = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const exitSelection = () => setSelected([])

  /** 打开合集选择器：懒加载合集列表 */
  const openPicker = async () => {
    setPickerOpen(true)
    if (collections.length > 0 || collectionsLoading) return
    setCollectionsLoading(true)
    try {
      setCollections(await fetchCollections())
    } catch {
      Alert.alert('加载失败', '合集列表拉取失败，请检查网络')
    } finally {
      setCollectionsLoading(false)
    }
  }

  const handleBatchFavorite = async () => {
    if (!ensureBatchLimit(selected.length, MAX_BATCH, '批量收藏')) return
    const allFavorite = favoriteCount === selected.length
    setBusy(true)
    try {
      await batchFavorite(selected, !allFavorite)
      exitSelection()
    } catch {
      Alert.alert('操作失败', '批量收藏失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const handleBatchTrash = () => {
    if (!ensureBatchLimit(selected.length, MAX_BATCH, '批量移入垃圾桶')) return
    Alert.alert('移入垃圾桶', `将 ${selected.length} 张素材移入垃圾桶（可恢复），确定吗？`, [
      { text: '取消', style: 'cancel' },
      {
        text: '移入',
        style: 'destructive',
        onPress: async () => {
          setBusy(true)
          try {
            const n = await batchTrash(selected)
            exitSelection()
            Alert.alert('已移入垃圾桶', `${n} 张素材已移入垃圾桶，可在 Web 端恢复`)
          } catch {
            Alert.alert('操作失败', '批量移入垃圾桶失败，请稍后重试')
          } finally {
            setBusy(false)
          }
        },
      },
    ])
  }

  const handlePickCollection = async (collection: CollectionItem) => {
    if (!ensureBatchLimit(selected.length, MAX_COLLECTION_BATCH, '加入合集')) return
    setBusy(true)
    try {
      await addToCollection(collection.id, selected)
      setPickerOpen(false)
      exitSelection()
      Alert.alert('已加入合集', `已把 ${selected.length} 张素材加入「${collection.name}」`)
    } catch {
      Alert.alert('操作失败', '加入合集失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const handleCreateCollection = async (name: string) => {
    if (!ensureBatchLimit(selected.length, MAX_COLLECTION_BATCH, '加入合集')) return
    setBusy(true)
    try {
      const created = await createCollection(name)
      setCollections((prev) => [created, ...prev])
      await addToCollection(created.id, selected)
      setPickerOpen(false)
      exitSelection()
      Alert.alert('已创建并加入', `合集「${created.name}」已创建，加入 ${selected.length} 张素材`)
    } catch {
      Alert.alert('操作失败', '新建合集失败，名称可能重复')
    } finally {
      setBusy(false)
    }
  }

  const renderCard = ({ item }: { item: Inspiration }) => {
    // 后端字段可能脏（tags 不是数组、元素缺 tag、id 为空）：兜底成空数组，
    // 否则 slice/map 直接抛错 → 整棵树卸载（真机白屏）
    const tags = Array.isArray(item.tags) ? item.tags.filter((t) => t && t.tag) : []
    const isSelected = selected.includes(item.id)

    return (
      <TouchableOpacity
        testID={`card-${item.id}`}
        style={styles.card}
        activeOpacity={0.8}
        onPress={() => (selectionMode ? toggleSelect(item.id) : router.push(`/detail/${item.id}`))}
        onLongPress={() => toggleSelect(item.id)}
        delayLongPress={280}
      >
        <Image
          source={{
            uri: item.thumbnail_path
              ? getFileUrl(apiBaseUrl, item.thumbnail_path)
              : getFileUrl(apiBaseUrl, item.file_path),
          }}
          style={styles.cardImage}
        />

        {/* 标签 */}
        <View style={styles.cardTags}>
          {tags.slice(0, 3).map((t, idx) => (
            <View key={`${item.id}-tag-${idx}`} style={styles.tag}>
              <Text style={styles.tagText} numberOfLines={1}>
                {String(t.tag.name ?? '')}
              </Text>
            </View>
          ))}
        </View>

        {/* 来源标识 */}
        <View style={styles.cardBadge}>
          <Text style={styles.cardBadgeText}>{sourceLabel(item.source_type)}</Text>
        </View>

        {/* 视频角标：缩略图是封面帧，点进去才是播放器 */}
        {item.media_type === 'video' && (
          <View style={styles.videoBadge}>
            <Icon name="play" size={10} color="#fff" />
            <Text style={styles.videoBadgeText}>视频</Text>
          </View>
        )}

        {/* 收藏按钮（多选态隐藏，避免与勾选冲突） */}
        {!selectionMode && (
          <TouchableOpacity
            style={styles.favBtn}
            onPress={() => toggleFavorite(item.id, !item.is_favorite)}
          >
            <Icon
              name={item.is_favorite ? 'heart' : 'heart-outline'}
              size={18}
              color={item.is_favorite ? '#ef4444' : '#fff'}
            />
          </TouchableOpacity>
        )}

        {/* 多选勾选 */}
        {isSelected && (
          <View style={styles.checkOverlay}>
            <Icon name="checkmark" size={22} color="#fff" />
          </View>
        )}
      </TouchableOpacity>
    )
  }

  return (
    <View style={styles.container}>
      {/* 顶部入口：合集 / 设置 */}
      <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
        <Text style={styles.title}>素材库</Text>
        <View style={styles.topActions}>
          <TouchableOpacity onPress={() => router.push('/collections')} hitSlop={10}>
            <Icon name="albums-outline" size={22} color="#6366f1" />
          </TouchableOpacity>
          <TouchableOpacity onPress={() => router.push('/settings')} hitSlop={10}>
            <Icon name="settings-outline" size={22} color="#6366f1" />
          </TouchableOpacity>
        </View>
      </View>

      <FilterBar
        filter={filter}
        sort={sort}
        onFilter={(next) => void setFilter(next)}
        onSort={(s) => void setSort(s)}
      />

      <FlatList
        data={items}
        renderItem={renderCard}
        keyExtractor={(item) => item.id}
        numColumns={2}
        contentContainerStyle={styles.grid}
        columnWrapperStyle={styles.row}
        onEndReached={onEndReached}
        onEndReachedThreshold={0.5}
        extraData={selected}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Icon name="images-outline" size={64} color="#d1d5db" />
            <Text style={styles.emptyText}>
              {filter.is_favorite
                ? '还没有收藏的素材'
                : filter.tag_status === 'untagged'
                  ? '没有未标注的素材'
                  : filter.tag_status === 'tagged'
                    ? '还没有已标注的素材'
                    : '还没有素材'}
            </Text>
            <Text style={styles.emptySubtext}>去采集或上传一些穿搭图片吧</Text>
          </View>
        }
      />

      {/* 多选操作栏 */}
      {selectionMode && (
        <SelectionBar
          count={selected.length}
          favoriteCount={favoriteCount}
          busy={busy}
          onToggleFavorite={handleBatchFavorite}
          onAddToCollection={() => void openPicker()}
          onTrash={handleBatchTrash}
          onCancel={exitSelection}
        />
      )}

      <CollectionPicker
        visible={pickerOpen}
        count={selected.length}
        collections={collections}
        loading={collectionsLoading}
        saving={busy}
        onClose={() => setPickerOpen(false)}
        onPick={handlePickCollection}
        onCreate={handleCreateCollection}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
    backgroundColor: '#fff',
  },
  title: { fontSize: 20, fontWeight: '600', color: '#111827' },
  topActions: { flexDirection: 'row', alignItems: 'center', gap: 16 },
  grid: { padding: 12, paddingBottom: 24 },
  row: { gap: 12, marginBottom: 12 },
  card: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: 10,
    overflow: 'hidden',
    aspectRatio: 0.75,
  },
  cardImage: { width: '100%', height: '100%', backgroundColor: '#f3f4f6' },
  cardTags: {
    position: 'absolute',
    bottom: 6,
    left: 6,
    right: 6,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 4,
  },
  tag: {
    backgroundColor: 'rgba(0,0,0,0.45)',
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 3,
    maxWidth: '48%',
  },
  tagText: { fontSize: 9, color: '#fff' },
  cardBadge: {
    position: 'absolute',
    top: 6,
    left: 6,
    backgroundColor: 'rgba(0,0,0,0.45)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  cardBadgeText: { fontSize: 10, color: '#fff' },
  favBtn: {
    position: 'absolute',
    top: 6,
    right: 6,
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: 'rgba(0,0,0,0.3)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  videoBadge: {
    position: 'absolute',
    bottom: 6,
    right: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: 'rgba(0,0,0,0.55)',
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 8,
  },
  videoBadgeText: { fontSize: 9, color: '#fff' },
  checkOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(99,102,241,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#6366f1',
  },
  empty: { paddingTop: 80, alignItems: 'center', gap: 6 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
  emptySubtext: { fontSize: 12, color: '#d1d5db' },
})
