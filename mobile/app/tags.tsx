/** 标签轻量管理：浏览 + 搜索 + 改名。
 *
 * 只做三件事（见 docs/移动端.md）：合并/删除/颜色/排序留在 Web ——
 * 破坏性操作需要对比多个标签，手机上误触代价高。
 *
 * 审查修复：
 * - P1-6 加载改骨架行，删除「正在加载标签库…（2 万+ 个，首次约需 8 秒）」这种技术文案
 * - P1-9 改名弹层换 `ui/BottomSheet`
 * - P1-10 分类中文名统一走 `constants/labels.ts`
 */

import { useEffect, useMemo, useState } from 'react'
import { FlatList, Pressable, StyleSheet, TextInput, View } from 'react-native'
import AppText from '../components/ui/AppText'
import Button from '../components/ui/Button'
import BottomSheet from '../components/ui/BottomSheet'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonList } from '../components/ui/Skeleton'
import Icon from '../components/Icon'
import { fetchTagGroups, invalidateTagCache, renameTag, type TagCategoryGroup } from '../services/api'
import { tagCategoryLabel } from '../constants/labels'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'

/** 不带搜索词时最多展示多少（标签库 2 万+，必须限量 + 虚拟化） */
const DEFAULT_LIMIT = 80
const SEARCH_LIMIT = 200

interface FlatTag {
  id: number
  name: string
  category: string
}

export default function TagsScreen() {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const [groups, setGroups] = useState<TagCategoryGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  /** 正在改名的标签 */
  const [editing, setEditing] = useState<FlatTag | null>(null)
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  /** 改名失败原因（必须显示在弹层里：列表区的错误会被遮罩挡住） */
  const [renameError, setRenameError] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      // 走会话级缓存：同一个 App 生命周期内只付一次 8 秒
      setGroups(await fetchTagGroups())
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const all = useMemo<FlatTag[]>(() => {
    const flat: FlatTag[] = []
    for (const g of groups) {
      for (const t of g.tags) flat.push({ id: t.id, name: t.name, category: g.category })
    }
    return flat
  }, [groups])

  const q = query.trim().toLowerCase()
  const visibleTags = useMemo(() => {
    if (!q) return all.slice(0, DEFAULT_LIMIT)
    return all.filter((t) => t.name.toLowerCase().includes(q)).slice(0, SEARCH_LIMIT)
  }, [all, q])

  const openEdit = (tag: FlatTag) => {
    setEditing(tag)
    setDraftName(tag.name)
    setRenameError('')
  }

  /** 关闭改名弹层（错误提示一并清掉） */
  const closeEdit = () => {
    setEditing(null)
    setRenameError('')
  }

  const submitRename = async () => {
    if (!editing) return
    const name = draftName.trim()
    if (!name || name === editing.name) {
      closeEdit()
      return
    }
    setSaving(true)
    setRenameError('')
    try {
      await renameTag(editing.id, name)
      // 改名后让其它页面（标签筛选/编辑器）下次拿到最新数据
      invalidateTagCache()
      // 本地同步：直接改名字，避免整库重拉（2 万+ 个标签太重）
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          tags: g.tags.map((t) => (t.id === editing.id ? { ...t, name } : t)),
        })),
      )
      closeEdit()
    } catch {
      setRenameError('改名失败：名称可能重复')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonList rows={8} />
      </View>
    )
  }

  if (error && groups.length === 0) {
    return (
      <View style={styles.container}>
        <ErrorState onRetry={() => void load()} />
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <View style={styles.searchBar}>
        <Icon name="search" size={theme.icon.sm} color={theme.color.textTertiary} />
        <TextInput
          style={styles.searchInput}
          value={query}
          onChangeText={setQuery}
          placeholder="搜索标签"
          placeholderTextColor={theme.color.textTertiary}
          autoCorrect={false}
        />
        {query.length > 0 ? (
          <Pressable onPress={() => setQuery('')} hitSlop={10} accessibilityLabel="清空搜索词">
            <Icon name="close-circle" size={theme.icon.sm} color={theme.color.textDisabled} />
          </Pressable>
        ) : null}
      </View>

      <AppText variant="caption" color="textTertiary" style={styles.hint}>
        {q
          ? `匹配 ${visibleTags.length} 个${visibleTags.length >= SEARCH_LIMIT ? '（仅显示前 200）' : ''}`
          : `共 ${all.length} 个标签，输入关键词缩小范围`}
      </AppText>

      <FlatList
        data={visibleTags}
        keyExtractor={(t) => String(t.id)}
        initialNumToRender={14}
        windowSize={6}
        removeClippedSubviews
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <AppText variant="bodySm" color="textTertiary" style={styles.emptyText}>
            没有匹配的标签
          </AppText>
        }
        renderItem={({ item }) => (
          <Pressable
            testID={`tag-${item.id}`}
            style={({ pressed }) => [styles.row, pressed && styles.pressed]}
            onPress={() => openEdit(item)}
            accessibilityRole="button"
          >
            <AppText variant="body" numberOfLines={1} style={styles.rowName}>
              {item.name}
            </AppText>
            <AppText variant="caption" color="textTertiary">
              {tagCategoryLabel(item.category)}
            </AppText>
            <Icon name="create-outline" size={theme.icon.sm} color={theme.color.textDisabled} />
          </Pressable>
        )}
      />

      {/* 改名弹层 */}
      <BottomSheet
        visible={editing !== null}
        title="重命名标签"
        onClose={closeEdit}
        testID="rename-sheet"
        footer={
          <>
            <Button title="取消" variant="secondary" onPress={closeEdit} />
            <Button title="保存" onPress={() => void submitRename()} loading={saving} />
          </>
        }
      >
        <View style={styles.sheetBody}>
          <AppText variant="caption" color="textTertiary">
            原名：{editing?.name ?? ''}
          </AppText>
          <TextInput
            style={styles.input}
            value={draftName}
            onChangeText={setDraftName}
            autoFocus
            placeholder="新名称"
            placeholderTextColor={theme.color.textTertiary}
            autoCorrect={false}
          />
          {renameError !== '' ? (
            <AppText variant="caption" color="dangerText">
              {renameError}
            </AppText>
          ) : null}
        </View>
      </BottomSheet>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    searchBar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      backgroundColor: t.color.bgSurface,
      margin: t.space.md,
      paddingHorizontal: t.space.md,
      minHeight: 42,
      borderRadius: t.radius.control,
      borderWidth: 1,
      borderColor: t.color.border,
    },
    searchInput: { flex: 1, fontSize: t.font.body.fontSize, color: t.color.textPrimary },
    hint: { paddingHorizontal: t.space.lg, paddingBottom: t.space.sm },
    list: { paddingHorizontal: t.space.md, paddingBottom: t.space.xxl },
    row: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      backgroundColor: t.color.bgSurface,
      paddingHorizontal: t.space.md,
      minHeight: 48,
      marginBottom: t.space.xs,
      borderRadius: t.radius.control,
    },
    rowName: { flex: 1 },
    pressed: { backgroundColor: t.color.bgSubtle },
    emptyText: { textAlign: 'center', paddingVertical: t.space.xxl },
    sheetBody: { paddingHorizontal: t.space.lg, paddingBottom: t.space.md, gap: t.space.sm },
    input: {
      minHeight: t.touch,
      borderWidth: 1,
      borderColor: t.color.border,
      borderRadius: t.radius.control,
      paddingHorizontal: t.space.md,
      fontSize: t.font.body.fontSize,
      color: t.color.textPrimary,
    },
  })
