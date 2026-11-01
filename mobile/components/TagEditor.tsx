/** 标签编辑弹层：搜索标签库、勾选新增、标记移除，**保存时统一提交**。
 *
 * 性能前提（2026-10-24 真机踩坑）：标签库有 **2 万+** 个标签，如果把全量标签
 * 一次性渲染成 chip 墙，JS 线程会直接卡死到 ANR。所以这里：
 * 1) 用 FlatList 虚拟列表（只渲染可视区的十几行）；
 * 2) 默认只显示前 60 个并提示「输入关键词搜索」，搜索时最多显示 200 条；
 * 3) 已关联标签单独一行常驻，数量少、可点即标记移除。
 *
 * 审查修复：
 * - P1-8 改前点已关联标签**立刻**调接口删除，误触一下就没了且无法撤销；
 *   现在点一下只是「标记移除」（再点取消），点「保存」才真正提交，与新增同一提交语义。
 * - P1-9 弹层换成 `ui/BottomSheet`：安全区、遮罩可点关闭、键盘避让、无硬编码高度。
 */

import { useEffect, useMemo, useState } from 'react'
import { FlatList, Pressable, StyleSheet, TextInput, View } from 'react-native'
import Icon from './Icon'
import AppText from './ui/AppText'
import Button from './ui/Button'
import Chip from './ui/Chip'
import BottomSheet from './ui/BottomSheet'
import { useStyles, useTheme } from '../hooks/useTheme'
import { tagCategoryLabel } from '../constants/labels'
import type { InspirationTag, TagCategoryGroup } from '../services/api'
import type { Theme } from '../constants/theme'

/** 不带搜索词时最多展示多少个标签 */
const DEFAULT_LIMIT = 60
/** 带搜索词时最多展示多少个结果 */
const SEARCH_LIMIT = 200

interface Props {
  visible: boolean
  /** 标签库加载中 */
  loading: boolean
  /** 全部标签（按类别分组） */
  groups: TagCategoryGroup[]
  /** 当前素材已关联的标签 */
  current: InspirationTag[]
  onClose: () => void
  /** 统一提交：新增标签名 + 待移除的标签 id */
  onSubmit: (payload: { add: string[]; remove: number[] }) => void
  /** 提交中（禁按钮） */
  saving?: boolean
  /** 标签库加载失败（弱网下常见，给重试入口而不是空白） */
  loadError?: boolean
  onRetry?: () => void
}

interface FlatTag {
  id: number
  name: string
  category: string
}

export default function TagEditor({
  visible,
  loading,
  groups,
  current,
  onClose,
  onSubmit,
  saving = false,
  loadError = false,
  onRetry,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const [query, setQuery] = useState('')
  /** 本次勾选待新增的标签名 */
  const [pendingAdd, setPendingAdd] = useState<string[]>([])
  /** 本次标记待移除的标签 id */
  const [pendingRemove, setPendingRemove] = useState<number[]>([])

  // 每次打开重置暂存态，避免上次的勾选残留
  useEffect(() => {
    if (visible) {
      setQuery('')
      setPendingAdd([])
      setPendingRemove([])
    }
  }, [visible])

  const currentIds = useMemo(() => new Set(current.map((t) => t.tag.id)), [current])

  const allTags = useMemo<FlatTag[]>(() => {
    const flat: FlatTag[] = []
    for (const g of groups) {
      for (const t of g.tags) flat.push({ id: t.id, name: t.name, category: g.category })
    }
    return flat
  }, [groups])

  const total = allTags.length
  const q = query.trim().toLowerCase()

  const visibleTags = useMemo<FlatTag[]>(() => {
    if (!q) return allTags.slice(0, DEFAULT_LIMIT)
    return allTags.filter((t) => t.name.toLowerCase().includes(q)).slice(0, SEARCH_LIMIT)
  }, [allTags, q])

  // 搜索词精确命中与否决定是否给「新建」入口；模糊匹配但未精确命中时也给，避免建重复
  const exactExists = useMemo(
    () => q.length > 0 && allTags.some((t) => t.name.toLowerCase() === q),
    [allTags, q],
  )
  const canCreate = q.length > 0 && !exactExists && !pendingAdd.includes(query.trim())

  const toggleAdd = (name: string) => {
    setPendingAdd((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }

  const toggleRemove = (tagId: number) => {
    setPendingRemove((prev) =>
      prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId],
    )
  }

  const handleSave = () => {
    const add = pendingAdd.length === 0 && canCreate ? [query.trim()] : pendingAdd
    onSubmit({ add, remove: pendingRemove })
  }

  const dirty = pendingAdd.length > 0 || pendingRemove.length > 0 || (canCreate && q.length > 0)

  return (
    <BottomSheet
      visible={visible}
      title="编辑标签"
      onClose={onClose}
      testID="tag-editor"
      footer={
        <>
          <AppText variant="caption" color="textTertiary" style={styles.footerInfo}>
            {pendingAdd.length > 0 ? `待添加 ${pendingAdd.length} 个` : ''}
            {pendingAdd.length > 0 && pendingRemove.length > 0 ? ' · ' : ''}
            {pendingRemove.length > 0 ? `待移除 ${pendingRemove.length} 个` : ''}
          </AppText>
          <Button
            title="取消"
            variant="secondary"
            onPress={onClose}
            disabled={saving}
            testID="tag-cancel"
          />
          <Button
            title="保存"
            onPress={handleSave}
            loading={saving}
            disabled={!dirty}
            testID="tag-save"
          />
        </>
      }
    >
      {/* 搜索 */}
      <View style={styles.searchRow}>
        <Icon name="search-outline" size={theme.icon.sm} color={theme.color.textTertiary} />
        <TextInput
          style={styles.searchInput}
          value={query}
          onChangeText={setQuery}
          placeholder="搜索或输入新标签"
          placeholderTextColor={theme.color.textTertiary}
          autoCorrect={false}
        />
        {query.length > 0 ? (
          <Pressable onPress={() => setQuery('')} hitSlop={10} accessibilityLabel="清空搜索词">
            <Icon name="close-circle" size={theme.icon.sm} color={theme.color.textDisabled} />
          </Pressable>
        ) : null}
      </View>

      {/* 已关联：常驻一行，点一下标记移除（再点取消） */}
      {current.length > 0 ? (
        <View style={styles.linkedBox}>
          <AppText variant="caption" color="textTertiary">
            已关联（点击标记移除）
          </AppText>
          <View style={styles.linkedChips}>
            {current.map((t) => {
              const marked = pendingRemove.includes(t.tag.id)
              return (
                <Pressable
                  key={t.tag.id}
                  onPress={() => toggleRemove(t.tag.id)}
                  disabled={saving}
                  accessibilityRole="button"
                  accessibilityState={{ selected: marked }}
                  style={[styles.linkedChip, marked && styles.linkedChipMarked]}
                  testID={`linked-tag-${t.tag.id}`}
                >
                  <Icon
                    name={marked ? 'close' : 'checkmark'}
                    size={theme.icon.sm}
                    color={marked ? theme.color.textOnMedia : theme.color.brandText}
                  />
                  <AppText
                    variant="bodySm"
                    color={marked ? 'textOnMedia' : 'brandText'}
                    numberOfLines={1}
                  >
                    {String(t.tag.name ?? '')}
                  </AppText>
                </Pressable>
              )
            })}
          </View>
        </View>
      ) : null}

      {/* 新建入口 */}
      {canCreate ? (
        <Pressable
          style={styles.createRow}
          onPress={() => toggleAdd(query.trim())}
          disabled={saving}
          accessibilityRole="button"
        >
          <Icon name="add-circle-outline" size={theme.icon.sm} color={theme.color.brandText} />
          <AppText variant="bodySm" color="brandText">
            新建标签「{query.trim()}」
          </AppText>
        </Pressable>
      ) : null}

      {/* 标签列表（虚拟化） */}
      {loading ? (
        <View style={styles.fillBox}>
          <AppText variant="bodySm" color="textSecondary">
            正在加载标签…
          </AppText>
        </View>
      ) : loadError && groups.length === 0 ? (
        <View style={styles.fillBox}>
          <AppText variant="bodySm" color="textSecondary">
            标签库加载失败
          </AppText>
          <Button title="重试" icon="refresh" variant="secondary" onPress={onRetry} />
        </View>
      ) : (
        <FlatList
          data={visibleTags}
          keyExtractor={(t) => String(t.id)}
          keyboardShouldPersistTaps="handled"
          style={styles.list}
          contentContainerStyle={styles.listContent}
          ListHeaderComponent={
            <AppText variant="caption" color="textTertiary" style={styles.listHeader}>
              {q ? `匹配 ${visibleTags.length} 个` : `共 ${total} 个标签，输入关键词缩小范围`}
            </AppText>
          }
          renderItem={({ item }) => {
            const linked = currentIds.has(item.id)
            const picked = pendingAdd.includes(item.name)
            return (
              <Pressable
                onPress={() => toggleAdd(item.name)}
                disabled={saving || linked}
                accessibilityRole="button"
                accessibilityState={{ selected: picked }}
                style={({ pressed }) => [
                  styles.tagRow,
                  picked && styles.tagRowPicked,
                  pressed && styles.pressed,
                ]}
              >
                <View style={styles.tagRowTexts}>
                  <AppText variant="body" color={picked ? 'brandText' : 'textPrimary'} numberOfLines={1}>
                    {item.name}
                  </AppText>
                  <AppText variant="caption" color="textTertiary">
                    {tagCategoryLabel(item.category)}
                  </AppText>
                </View>
                {linked ? (
                  <Chip label="已关联" selected />
                ) : picked ? (
                  <Icon name="checkmark-circle" size={theme.icon.md} color={theme.color.brandText} />
                ) : (
                  <Icon name="add-circle-outline" size={theme.icon.md} color={theme.color.textDisabled} />
                )}
              </Pressable>
            )
          }}
        />
      )}
    </BottomSheet>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    searchRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      marginHorizontal: t.space.lg,
      paddingHorizontal: t.space.md,
      minHeight: 44,
      borderRadius: t.radius.control,
      borderWidth: 1,
      borderColor: t.color.border,
      backgroundColor: t.color.bgSubtle,
    },
    searchInput: { flex: 1, fontSize: t.font.body.fontSize, color: t.color.textPrimary },
    linkedBox: { paddingHorizontal: t.space.lg, paddingTop: t.space.md, gap: t.space.sm },
    linkedChips: { flexDirection: 'row', flexWrap: 'wrap', gap: t.space.sm },
    linkedChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.xs,
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.xs,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.brandSubtle,
      maxWidth: '100%',
    },
    linkedChipMarked: { backgroundColor: t.color.dangerFill },
    createRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.xs,
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
    },
    fillBox: { paddingVertical: t.space.xxxl, alignItems: 'center', gap: t.space.md },
    list: { marginTop: t.space.sm, maxHeight: 320 },
    listContent: { paddingBottom: t.space.sm },
    listHeader: { paddingHorizontal: t.space.lg, paddingBottom: t.space.sm },
    tagRow: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
    },
    tagRowPicked: { backgroundColor: t.color.brandSubtle },
    tagRowTexts: { flex: 1, gap: 2 },
    pressed: { opacity: 0.7 },
    footerInfo: { flex: 1 },
  })
