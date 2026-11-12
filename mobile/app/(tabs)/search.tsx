/** 搜索页：关键词/标签筛选 + 分页 + 以图搜图 + 语义检索。
 *
 * 手机侧取舍（docs/移动端.md）：
 * - 标签筛选不再是「全量 chip 墙」（库里有 2 万+ 标签，会直接卡死），改为弹层搜索多选
 * - 结果分页（onEndReached），不再一次性拉到底
 * - 以图搜图是手机端**独占优势**：相册选图 → `/search/vector`（CLIP 图像向量）
 *
 * 审查修复：
 * - P0-7 结果列表 `keyboardShouldPersistTaps="handled"`：键盘未收起时点卡片一次即进详情
 * - P1-4 关键词/语义改 `SegmentedTab`（页面级模式切换）
 * - P2-5 初始态不再空白：最近搜索 + 常用标签
 * - 语义模式下标签筛选**禁用而非隐藏**（改前静默忽略已选标签，用户以为生效了）
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  View,
  TextInput,
  FlatList,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Alert,
  useWindowDimensions,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import { useRouter } from 'expo-router'
import ScreenHeader from '../../components/ui/ScreenHeader'
import SegmentedTab from '../../components/ui/SegmentedTab'
import Chip from '../../components/ui/Chip'
import MediaCard from '../../components/ui/MediaCard'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import AppText from '../../components/ui/AppText'
import Icon from '../../components/Icon'
import TagFilterSheet from '../../components/TagFilterSheet'
import { useInspirationStore, type Inspiration } from '../../hooks/useInspirations'
import { useStyles, useTheme } from '../../hooks/useTheme'
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
import { dominantColor } from '../../utils/dominantColor'
import { loadSearchHistory, pushSearchHistory } from '../../utils/searchHistory'
import { loadPopularTags, savePopularTags } from '../../utils/offlineCache'
import { gridColumns } from '../../utils/layout'
import type { Theme } from '../../constants/theme'

const PAGE_SIZE = 30
/** 初始态展示几个常用标签 */
const POPULAR_TAG_COUNT = 8

type SearchMode = 'keyword' | 'semantic'

const MODE_TABS: { key: SearchMode; label: string }[] = [
  { key: 'keyword', label: '关键词' },
  { key: 'semantic', label: '语义' },
]

export default function SearchScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const { apiBaseUrl, toggleFavorite } = useInspirationStore()
  /** 收藏变更版本：详情页改收藏后，返回搜索页时本地 results 需要刷新 */
  const favoriteRevision = useInspirationStore((s) => s.favoriteRevision)
  const { width } = useWindowDimensions()
  /** 平板/大屏切 3 列 */
  const columns = gridColumns(width)

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
  const [mode, setMode] = useState<SearchMode>('keyword')
  /** 当前结果是否来自语义检索 */
  const [semanticQuery, setSemanticQuery] = useState<string | null>(null)
  /** 最近搜索（本地历史，P2-5） */
  const [history, setHistory] = useState<string[]>([])
  /** 标签库拉取失败时的离线常用标签（P1 弱网/离线） */
  const [cachedPopular, setCachedPopular] = useState<string[]>([])
  /** 搜索请求序号：翻页在飞时重新搜索/以图搜图，旧响应直接丢弃，避免混入新结果 */
  const searchSeqRef = useRef(0)

  useEffect(() => {
    void loadTags()
    void loadSearchHistory().then(setHistory)
    // 标签库拉不到时（断网/后端未启动）用上次缓存的常用标签兜底，初始态不至于空着
    void loadPopularTags().then(setCachedPopular)
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

  /** 常用标签（真实来源）：按 usage_count 取前 8（后端分组响应自带使用次数） */
  const fetchedPopular = useMemo(() => {
    const all: { name: string; count: number }[] = []
    for (const group of tagGroups) {
      for (const tag of group.tags) {
        all.push({ name: tag.name, count: tag.usage_count ?? 0 })
      }
    }
    const seen = new Set<string>()
    return all
      .sort((a, b) => b.count - a.count)
      .filter((tag) => {
        if (seen.has(tag.name)) return false
        seen.add(tag.name)
        return true
      })
      .slice(0, POPULAR_TAG_COUNT)
      .map((tag) => tag.name)
  }, [tagGroups])

  /** 展示用常用标签：拉到了用真实数据，拉不到（标签库为空）用离线缓存 */
  const popularTags = fetchedPopular.length > 0 ? fetchedPopular : cachedPopular

  // 拉到真实数据就刷新离线缓存（下次断网时初始态还能用）
  useEffect(() => {
    if (fetchedPopular.length > 0) void savePopularTags(fetchedPopular)
  }, [fetchedPopular])

  /** 执行搜索（page=1 重置结果） */
  const doSearch = useCallback(
    async (nextPage = 1, overrideKeyword?: string) => {
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
        const text = (overrideKeyword ?? searchText).trim()
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
          setHistory(await pushSearchHistory(text))
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
        const keyword = (overrideKeyword ?? searchText).trim()
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
        if (nextPage === 1 && keyword) setHistory(await pushSearchHistory(keyword))
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

  /** 详情页改收藏后会递增 favoriteRevision：搜索页的 results 是本地状态，
   * 单靠 store.items 同步不到；版本变化时重跑当前搜索。 */
  const lastFavoriteRevisionRef = useRef(0)
  useEffect(() => {
    if (favoriteRevision === lastFavoriteRevisionRef.current) return
    lastFavoriteRevisionRef.current = favoriteRevision
    if (searched) void doSearch(1)
  }, [favoriteRevision, searched, doSearch])

  /** 图像向量检索（相册选图与拍照共用）：拿到本地图片地址后跑 `/search/vector` */
  const runImageSearch = async (uri: string) => {
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
    await runImageSearch(picked.assets[0].uri)
  }

  /**
   * 拍照搜图（P0-5）：逛街现场拍同款 → （可选裁剪主体）→ 图像向量检索。
   *
   * `allowsEditing: true` 会先进系统裁剪界面：直接把整张图确认掉就是「使用原图」，
   * 框选则只留主体（背景杂乱的橱窗照裁掉背景后相似度明显更准）。
   * 不传 `aspect`：Android 上会锁死宽高比，反而不利于按主体自由框选。
   */
  const doCameraSearch = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在系统设置中允许使用相机')
      return
    }
    const picked = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      quality: 0.8,
      allowsEditing: true,
    })
    if (picked.canceled || !picked.assets?.[0]) return
    await runImageSearch(picked.assets[0].uri)
  }

  /** 切换模式：语义模式不支持标签筛选，清掉已选标签（避免「选了但没生效」） */
  const changeMode = (nextKey: string) => {
    const next = nextKey as SearchMode
    if (next === mode) return
    setMode(next)
    if (next === 'semantic') setSelectedTags([])
    setSearched(false)
    setResults([])
    setTotal(0)
    setError('')
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
      setResults((prev) => prev.map((r) => (r.id === item.id ? { ...r, is_favorite: newState } : r)))
    } catch {
      Alert.alert('操作失败', '收藏操作失败，请稍后重试')
    }
  }

  /** 点最近搜索/常用标签：先落输入框，再用显式关键词发起搜索 */
  const applyHistory = (term: string) => {
    setSearchText(term)
    void doSearch(1, term)
  }

  const initial = !searched && results.length === 0
  const showError = Boolean(error) && results.length === 0
  const noResult = searched && !loading && !error && total === 0

  return (
    <View style={styles.container}>
      <ScreenHeader title="搜索" />

      {/* 搜索栏：输入框内嵌搜索按钮（§4.4） */}
      <View style={styles.searchBar}>
        <Icon name="search" size={theme.icon.md} color={theme.color.textTertiary} />
        <TextInput
          testID="search-input"
          style={styles.searchInput}
          placeholder="搜标签名 / 作者 / 文件名"
          placeholderTextColor={theme.color.textTertiary}
          value={searchText}
          onChangeText={setSearchText}
          onSubmitEditing={() => void doSearch(1)}
          returnKeyType="search"
          blurOnSubmit
          autoCorrect={false}
        />
        {searchText.length > 0 ? (
          <Pressable onPress={() => setSearchText('')} hitSlop={10} accessibilityLabel="清空输入">
            <Icon name="close-circle" size={theme.icon.md} color={theme.color.textDisabled} />
          </Pressable>
        ) : null}
        <Pressable
          testID="search-submit"
          onPress={() => void doSearch(1)}
          accessibilityRole="button"
          accessibilityLabel="搜索"
          style={styles.submit}
        >
          <AppText variant="bodySm" color="brandText">
            搜索
          </AppText>
        </Pressable>
      </View>

      {/* 检索模式：关键词（标签/全文）或语义（向量） */}
      <View style={styles.modeRow}>
        <SegmentedTab
          items={MODE_TABS}
          value={mode}
          onChange={changeMode}
          testIDPrefix="mode"
        />
      </View>

      {/* 条件行：标签筛选 + 以图搜图（语义模式下禁用而非隐藏） */}
      <View style={styles.condRow}>
        <Chip
          testID="cond-tag"
          icon="pricetags-outline"
          label={`标签${selectedTags.length > 0 ? ` (${selectedTags.length})` : ''}`}
          disabled={mode === 'semantic'}
          onPress={() => setTagSheetOpen(true)}
        />
        <Chip
          testID="cond-image"
          icon="images-outline"
          label="以图搜图"
          disabled={mode === 'semantic'}
          onPress={() => void doImageSearch()}
        />
        <Chip
          testID="cond-camera"
          icon="camera-outline"
          label="拍照搜图"
          disabled={mode === 'semantic'}
          onPress={() => void doCameraSearch()}
        />
        {mode === 'semantic' ? (
          <AppText variant="micro" color="textTertiary" style={styles.flex}>
            语义检索不支持标签筛选
          </AppText>
        ) : null}
      </View>

      {/* 已选标签 */}
      {selectedTags.length > 0 && mode === 'keyword' ? (
        <View style={styles.selectedRow}>
          {selectedTags.map((name) => (
            <Chip
              key={name}
              label={name}
              selected
              onClear={() => setSelectedTags((prev) => prev.filter((n) => n !== name))}
            />
          ))}
        </View>
      ) : null}

      {/* 以图搜图预览 */}
      {imageQueryUri ? (
        <View style={styles.queryRow}>
          <AppText variant="caption" color="textSecondary">
            以这张图搜到的相似素材
          </AppText>
        </View>
      ) : null}

      {/* 初始态：最近搜索 + 常用标签（P2-5） */}
      {initial ? (
        <View style={styles.initialBox}>
          {history.length > 0 ? (
            <View style={styles.section}>
              <AppText variant="subtitle">最近搜索</AppText>
              <View style={styles.chipWrap}>
                {history.map((term) => (
                  <Chip key={term} label={term} onPress={() => applyHistory(term)} />
                ))}
              </View>
            </View>
          ) : null}
          {popularTags.length > 0 ? (
            <View style={styles.section}>
              <AppText variant="subtitle">常用标签</AppText>
              <View style={styles.chipWrap}>
                {popularTags.map((name) => (
                  <Chip key={name} label={name} onPress={() => applyHistory(name)} />
                ))}
              </View>
            </View>
          ) : null}
          {history.length === 0 && popularTags.length === 0 ? (
            <EmptyState
              icon="search-outline"
              title="搜点什么"
              description="支持标签名、作者、文件名；也可以切到语义模式描述一句话"
            />
          ) : null}
        </View>
      ) : null}

      {/* 结果计数吸顶 */}
      {results.length > 0 ? (
        <View style={styles.countBar}>
          <AppText variant="caption" color="textTertiary">
            {semanticQuery ? `语义匹配 ${total} 条` : `找到 ${total} 条结果`}
          </AppText>
        </View>
      ) : null}

      {loading && results.length === 0 ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator color={theme.color.brandFill} />
        </View>
      ) : null}

      {showError ? <ErrorState title={error} onRetry={() => void doSearch(1)} /> : null}

      {noResult ? (
        <EmptyState
          icon="search-outline"
          title={semanticQuery ? '语义检索没有匹配' : '没有找到匹配的素材'}
          description="换个关键词，或清除标签再试"
          actionLabel={selectedTags.length > 0 ? '清除标签' : undefined}
          onAction={
            selectedTags.length > 0
              ? () => {
                  setSelectedTags([])
                  void doSearch(1)
                }
              : undefined
          }
        />
      ) : null}

      {results.length > 0 ? (
        <FlatList
          data={results}
          renderItem={({ item }) => (
            <MediaCard
              uri={
                item.thumbnail_path
                  ? getFileUrl(apiBaseUrl, item.thumbnail_path)
                  : getFileUrl(apiBaseUrl, item.file_path)
              }
              tags={
                Array.isArray(item.tags)
                  ? item.tags.filter((t) => t && t.tag).map((t) => String(t.tag.name ?? ''))
                  : []
              }
              badgeText={sourceLabel(item.source_type)}
              video={item.media_type === 'video'}
              favorite={item.is_favorite}
              showFavorite
              onToggleFavorite={() => void handleToggleFavorite(item)}
              placeholderColor={dominantColor(item.dominant_colors) ?? undefined}
              onPress={() => router.push(`/detail/${item.id}`)}
            />
          )}
          keyExtractor={(item) => item.id}
          numColumns={columns}
          key={`search-${columns}`}
          columnWrapperStyle={styles.row}
          contentContainerStyle={styles.resultsGrid}
          onEndReached={onEndReached}
          onEndReachedThreshold={0.5}
          // P0-7：键盘未收起时第一次点击就能进详情
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          ListFooterComponent={
            loadingMore ? (
              <ActivityIndicator style={styles.footer} color={theme.color.brandFill} />
            ) : null
          }
        />
      ) : null}

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

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    flex: { flex: 1 },
    searchBar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      marginHorizontal: t.space.md,
      marginTop: t.space.md,
      paddingLeft: t.space.md,
      paddingRight: t.space.xs,
      minHeight: 46,
      borderRadius: t.radius.control,
      borderWidth: 1,
      borderColor: t.color.border,
      backgroundColor: t.color.bgSurface,
    },
    searchInput: { flex: 1, fontSize: t.font.body.fontSize, color: t.color.textPrimary },
    submit: { minHeight: t.touch, justifyContent: 'center', paddingHorizontal: t.space.sm },
    modeRow: { paddingHorizontal: t.space.md, paddingTop: t.space.md },
    condRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.sm,
    },
    selectedRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: t.space.sm,
      paddingHorizontal: t.space.md,
      paddingBottom: t.space.sm,
    },
    queryRow: { paddingHorizontal: t.space.md, paddingBottom: t.space.sm },
    initialBox: { padding: t.space.lg, gap: t.space.xxl },
    section: { gap: t.space.md },
    chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: t.space.sm },
    countBar: {
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgPage,
    },
    loadingBox: { paddingVertical: t.space.xxl, alignItems: 'center' },
    resultsGrid: { paddingHorizontal: t.space.md, paddingBottom: t.space.xxl },
    row: { gap: t.space.md, marginBottom: t.space.md },
    footer: { paddingVertical: t.space.lg },
  })
