/** 人物浏览：穿搭博主 / 模特两个 Tab，搜索 + 素材数排序 + 点击进作品墙。
 *
 * 手机侧取舍（docs/移动端.md）：只做浏览，不做新建/编辑/合并/人脸扫描 ——
 * 那些是维护工作，留在 Web。
 *
 * 审查修复：
 * - P0-2 输入与请求解耦（300ms 防抖）：改前 `load` 依赖 `search`，每敲一个字就发一次请求、
 *   且 `nextPage===1` 会 `setLoading(true)` 把整页列表卸载成整屏 spinner；
 *   现在只有首屏/切 Tab 才整屏骨架，改查询时保留旧结果 + 行内 loading。
 * - P1-4 Tab 改 `SegmentedTab`（页面级切换，不再是筛选胶囊）。
 * - P1-5 错误与空态分开（断网时不再显示「还没有穿搭博主」）。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  TextInput,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import ListRow from '../components/ui/ListRow'
import SegmentedTab from '../components/ui/SegmentedTab'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonList } from '../components/ui/Skeleton'
import AppText from '../components/ui/AppText'
import Icon from '../components/Icon'
import { fetchPersons, getFileUrl, type Person, type PersonKind } from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'
import { useStyles, useTheme } from '../hooks/useTheme'
import { platformLabel } from '../constants/labels'
import type { Theme } from '../constants/theme'

const PAGE_SIZE = 20

/** 搜索输入防抖时长（P0-2） */
const SEARCH_DEBOUNCE_MS = 300

const KIND_TABS: { key: PersonKind; label: string }[] = [
  { key: 'blogger', label: '穿搭博主' },
  { key: 'model', label: '模特' },
]

export default function PersonsScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)

  const [kind, setKind] = useState<PersonKind>('blogger')
  /** 输入框里的原始文字（不做请求触发） */
  const [searchInput, setSearchInput] = useState('')
  /** 防抖后的查询词（请求只认它） */
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<Person[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  /** 翻页互斥 + 请求序号：避免慢网重复触发 onEndReached 时追加重复页 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)
  /** 当前是否已有数据（决定首屏骨架 vs 行内 loading） */
  const itemsRef = useRef<Person[]>([])
  itemsRef.current = items

  // 输入防抖：停手 300ms 才真正查（P0-2）
  useEffect(() => {
    if (searchInput === query) return
    const timer = setTimeout(() => setQuery(searchInput.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [searchInput, query])

  const load = useCallback(
    async (nextPage = 1, nextKind = kind, keyword = query) => {
      if (nextPage > 1 && loadingMoreRef.current) return
      const seq = ++requestSeqRef.current
      if (nextPage > 1) {
        loadingMoreRef.current = true
        setLoadingMore(true)
      } else if (itemsRef.current.length === 0) {
        setLoading(true)
      }
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
          setLoadingMore(false)
          setRefreshing(false)
        }
      }
    },
    [kind, query],
  )

  useEffect(() => {
    void load(1)
  }, [load])

  /** 切换 Tab：清空旧数据并整屏骨架（这是「换内容」，不是「改查询」） */
  const changeKind = (nextKey: string) => {
    const next = nextKey as PersonKind
    if (next === kind) return
    setKind(next)
    setItems([])
    setPage(1)
    setError('')
    setLoading(true)
  }

  const onRefresh = () => {
    setRefreshing(true)
    void load(1)
  }

  const onEndReached = () => {
    if (!loading && !loadingMoreRef.current && items.length < total) void load(page + 1)
  }

  const clearSearch = () => {
    setSearchInput('')
    setQuery('')
  }

  const emptyTitle = query
    ? `没有匹配「${query}」的人物`
    : kind === 'blogger'
      ? '还没有穿搭博主'
      : '还没有模特'

  const showSkeleton = loading && items.length === 0
  const showError = !loading && Boolean(error) && items.length === 0

  return (
    <View style={styles.container}>
      <View style={styles.toolbar}>
        <SegmentedTab
          items={KIND_TABS}
          value={kind}
          onChange={changeKind}
          testIDPrefix="person-kind"
        />

        <View style={styles.searchBar}>
          <Icon name="search" size={theme.icon.sm} color={theme.color.textTertiary} />
          <TextInput
            style={styles.searchInput}
            value={searchInput}
            onChangeText={setSearchInput}
            onSubmitEditing={() => setQuery(searchInput.trim())}
            placeholder="搜人物名称"
            placeholderTextColor={theme.color.textTertiary}
            returnKeyType="search"
            blurOnSubmit
          />
          {searchInput.length > 0 ? (
            <Pressable onPress={clearSearch} hitSlop={10} accessibilityLabel="清除搜索词">
              <Icon name="close-circle" size={theme.icon.md} color={theme.color.textDisabled} />
            </Pressable>
          ) : null}
        </View>
      </View>

      {showSkeleton ? (
        <SkeletonList rows={6} />
      ) : showError ? (
        <ErrorState onRetry={() => void load(1)} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(p) => `${p.kind}-${p.id}`}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              colors={[theme.color.brandFill]}
              tintColor={theme.color.brandFill}
            />
          }
          onEndReached={onEndReached}
          onEndReachedThreshold={0.5}
          renderItem={({ item }) => (
            <ListRow
              testID={`person-${item.id}`}
              title={item.name}
              subtitle={`${platformLabel(item.platform)} · ${item.inspiration_count} 张${
                item.ip_location ? ` · ${item.ip_location}` : ''
              }`}
              leading={
                item.avatar_path ? (
                  <Image
                    source={{ uri: getFileUrl(apiBaseUrl, item.avatar_path) }}
                    style={styles.avatar}
                  />
                ) : (
                  <View style={[styles.avatar, styles.avatarEmpty]}>
                    <Icon name="person-outline" size={theme.icon.md} color={theme.color.brandText} />
                  </View>
                )
              }
              onPress={() => router.push(`/person/${item.id}?kind=${item.kind}`)}
            />
          )}
          ListEmptyComponent={
            <EmptyState
              icon="people-outline"
              title={emptyTitle}
              description={query ? '换个关键词试试' : undefined}
            />
          }
          ListFooterComponent={
            loadingMore ? (
              <ActivityIndicator style={styles.footer} color={theme.color.brandFill} />
            ) : items.length > 0 ? (
              <AppText variant="caption" color="textTertiary" style={styles.footerText}>
                共 {total} 位
              </AppText>
            ) : null
          }
        />
      )}

      {/* 改查询时的行内 loading：不清空旧结果（P0-2） */}
      {loading && items.length > 0 ? (
        <View style={styles.inlineLoading} pointerEvents="none">
          <ActivityIndicator size="small" color={theme.color.brandFill} />
        </View>
      ) : null}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    toolbar: {
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
      paddingTop: t.space.md,
      paddingBottom: t.space.sm,
      backgroundColor: t.color.bgSurface,
    },
    searchBar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      minHeight: t.touch,
      paddingHorizontal: t.space.md,
      borderRadius: t.radius.control,
      borderWidth: 1,
      borderColor: t.color.border,
      backgroundColor: t.color.bgSurface,
    },
    searchInput: { flex: 1, fontSize: t.font.body.fontSize, color: t.color.textPrimary },
    list: { paddingBottom: t.space.xxl },
    avatar: { width: 36, height: 36, borderRadius: t.radius.pill, backgroundColor: t.color.bgSubtle },
    avatarEmpty: { alignItems: 'center', justifyContent: 'center' },
    footer: { paddingVertical: t.space.lg },
    footerText: { textAlign: 'center', paddingVertical: t.space.md },
    inlineLoading: { position: 'absolute', top: 76, alignSelf: 'center' },
  })
