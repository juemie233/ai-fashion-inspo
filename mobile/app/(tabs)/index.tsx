/** 素材库：两列网格 + 筛选/排序 + 长按多选（批量收藏 / 加合集 / 移入垃圾桶）。
 *
 * 本页承载的审查修复：
 * - P0-1 三态：加载中骨架网格、失败错误态（带重试）、真空态（带去采集）
 * - P0-3 冷启动只发一次第一页请求（`init()` 不再顺带拉数据）
 * - P0-6 卡片底部渐变遮罩 + 11px 白字标签，标签与视频角标分行不重叠
 * - P1-2 自绘页头（22/28/600），无齿轮
 * - P1-3 卡片统一 `MediaCard`（3:4、圆角 14、角标位置固定）
 * - P1-7 排序改底部弹层单选，不再循环点四次
 * - P1-11 移除右上齿轮（设置走底部 Tab），保留「合集」并新增「选择」
 * - P2-3 多选可发现：页头「选择」按钮进入多选
 */

import { useState, useEffect, useCallback } from 'react'
import { View, FlatList, RefreshControl, StyleSheet, Alert } from 'react-native'
import { useRouter } from 'expo-router'
import ScreenHeader from '../../components/ui/ScreenHeader'
import IconButton from '../../components/ui/IconButton'
import MediaCard from '../../components/ui/MediaCard'
import FilterBar from '../../components/FilterBar'
import SelectionBar from '../../components/SelectionBar'
import CollectionPicker from '../../components/CollectionPicker'
import SortSheet from '../../components/SortSheet'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import Snackbar from '../../components/ui/Snackbar'
import { SkeletonGrid } from '../../components/ui/Skeleton'
import AppText from '../../components/ui/AppText'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { selectionFeedback, successFeedback } from '../../utils/haptics'
import {
  addToCollection,
  createCollection,
  fetchCollections,
  getFileUrl,
  type CollectionItem,
  type SortKey,
} from '../../services/api'
import { sourceLabel } from '../../utils/sourceLabel'
import { dominantColor } from '../../utils/dominantColor'
import type { Theme } from '../../constants/theme'

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
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const {
    items,
    loading,
    total,
    filter,
    sort,
    apiBaseUrl,
    error,
    fetchInspirations,
    fetchMore,
    toggleFavorite,
    setFilter,
    setSort,
    batchFavorite,
    batchTrash,
  } = useInspirationStore()

  const [refreshing, setRefreshing] = useState(false)
  /** 是否处于多选模式（与「已选数量」分开：可以从页头「选择」进入但不选任何素材） */
  const [selectionMode, setSelectionMode] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [sortOpen, setSortOpen] = useState(false)
  const [snackbar, setSnackbar] = useState('')
  const [snackbarVisible, setSnackbarVisible] = useState(false)

  // 合集选择器
  const [pickerOpen, setPickerOpen] = useState(false)
  const [collections, setCollections] = useState<CollectionItem[]>([])
  const [collectionsLoading, setCollectionsLoading] = useState(false)

  // 首次加载：唯一一次第一页请求（P0-3）
  useEffect(() => {
    void fetchInspirations()
  }, [])

  // 筛选/排序变化后列表已被替换：多选必须一起清掉，否则批量操作会落到看不见的旧素材上
  useEffect(() => {
    setSelected([])
    setSelectionMode(false)
  }, [filter, sort])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    setSelected([])
    await fetchInspirations()
    setRefreshing(false)
  }, [])

  const onEndReached = () => {
    if (items.length < total && !loading) {
      void fetchMore()
    }
  }

  const showSnack = (message: string) => {
    setSnackbar(message)
    setSnackbarVisible(true)
  }

  const selectedItems = items.filter((i) => selected.includes(i.id))
  const favoriteCount = selectedItems.filter((i) => i.is_favorite).length

  const toggleSelect = (id: string) => {
    selectionFeedback()
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const enterSelection = () => {
    selectionFeedback()
    setSelectionMode((prev) => !prev)
    setSelected([])
  }

  const exitSelection = () => {
    setSelected([])
    setSelectionMode(false)
  }

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
      const count = selected.length
      exitSelection()
      successFeedback()
      showSnack(allFavorite ? `已取消收藏 ${count} 张` : `已收藏 ${count} 张`)
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
      const count = selected.length
      setPickerOpen(false)
      exitSelection()
      successFeedback()
      showSnack(`已加入「${collection.name}」：${count} 张`)
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
      const count = selected.length
      setPickerOpen(false)
      exitSelection()
      successFeedback()
      showSnack(`已创建「${created.name}」并加入 ${count} 张`)
    } catch {
      Alert.alert('操作失败', '新建合集失败，名称可能重复')
    } finally {
      setBusy(false)
    }
  }

  const handleToggleFavorite = async (item: Inspiration) => {
    try {
      await toggleFavorite(item.id, !item.is_favorite)
      selectionFeedback()
    } catch {
      showSnack('收藏操作失败')
    }
  }

  const renderCard = ({ item }: { item: Inspiration }) => {
    // 后端字段可能脏（tags 不是数组、元素缺 tag、id 为空）：兜底成空数组，
    // 否则 slice/map 直接抛错 → 整棵树卸载（真机白屏）
    const tags = Array.isArray(item.tags)
      ? item.tags.filter((t) => t && t.tag).map((t) => String(t.tag.name ?? ''))
      : []

    return (
      <MediaCard
        testID={`card-${item.id}`}
        uri={
          item.thumbnail_path
            ? getFileUrl(apiBaseUrl, item.thumbnail_path)
            : getFileUrl(apiBaseUrl, item.file_path)
        }
        tags={tags}
        badgeText={sourceLabel(item.source_type)}
        video={item.media_type === 'video'}
        favorite={item.is_favorite}
        showFavorite={!selectionMode}
        onToggleFavorite={() => void handleToggleFavorite(item)}
        selected={selectionMode && selected.includes(item.id)}
        placeholderColor={dominantColor(item.dominant_colors) ?? undefined}
        onPress={() => (selectionMode ? toggleSelect(item.id) : router.push(`/detail/${item.id}`))}
        onLongPress={() => {
          if (!selectionMode) setSelectionMode(true)
          toggleSelect(item.id)
        }}
      />
    )
  }

  /** 空态文案随筛选条件区分（保留原有语义） */
  const emptyTitle = filter.is_favorite
    ? '还没有收藏的素材'
    : filter.tag_status === 'untagged'
      ? '没有未标注的素材'
      : filter.tag_status === 'tagged'
        ? '还没有已标注的素材'
        : '还没有素材'

  const firstLoad = loading && items.length === 0
  const showError = !loading && Boolean(error) && items.length === 0

  return (
    <View style={styles.container}>
      <ScreenHeader
        title="素材库"
        right={
          <>
            <IconButton
              name="albums-outline"
              color={theme.color.brandText}
              onPress={() => router.push('/collections')}
              accessibilityLabel="收藏合集"
            />
            <IconButton
              name={selectionMode ? 'close' : 'checkmark-circle-outline'}
              color={theme.color.brandText}
              onPress={enterSelection}
              accessibilityLabel={selectionMode ? '退出多选' : '选择素材'}
              testID="toggle-selection"
            />
          </>
        }
      />

      {selectionMode && selected.length === 0 ? (
        <View style={styles.selectionBanner}>
          <AppText variant="bodySm" color="textSecondary">
            点选要批量操作的素材
          </AppText>
        </View>
      ) : null}

      {/* 筛选栏在多选态保留：切换筛选会清空选择（避免批量操作落到看不见的旧素材） */}
      <FilterBar
        filter={filter}
        sort={sort}
        onFilter={(next) => void setFilter(next)}
        onOpenSort={() => setSortOpen(true)}
      />

      {error && items.length > 0 ? (
        <View style={styles.inlineError}>
          <AppText variant="caption" color="dangerText">
            {error}
          </AppText>
        </View>
      ) : null}

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
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            colors={[theme.color.brandFill]}
            tintColor={theme.color.brandFill}
          />
        }
        ListEmptyComponent={
          firstLoad ? (
            <SkeletonGrid count={8} columns={2} />
          ) : showError ? (
            <ErrorState onRetry={() => void fetchInspirations()} />
          ) : (
            <EmptyState
              title={emptyTitle}
              description="去采集或上传一些穿搭图片吧"
              actionLabel="去采集"
              onAction={() => router.push('/capture')}
            />
          )
        }
      />

      {/* 多选操作栏 */}
      {selectionMode ? (
        <SelectionBar
          count={selected.length}
          favoriteCount={favoriteCount}
          busy={busy}
          onToggleFavorite={handleBatchFavorite}
          onAddToCollection={() => void openPicker()}
          onTrash={handleBatchTrash}
          onCancel={exitSelection}
        />
      ) : null}

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

      {/* 排序：底部弹层单选（P1-7） */}
      <SortSheet
        visible={sortOpen}
        value={sort}
        onClose={() => setSortOpen(false)}
        onPick={(next: SortKey) => {
          setSortOpen(false)
          void setSort(next)
        }}
      />

      <Snackbar
        visible={snackbarVisible}
        message={snackbar}
        onHide={() => setSnackbarVisible(false)}
      />
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl },
    row: { gap: t.space.md, marginBottom: t.space.md },
    selectionBanner: {
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    inlineError: {
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
    },
  })
