/** 标签筛选弹层（搜索页用）：搜索 + 虚拟列表 + 多选。
 *
 * 为什么单独一个组件而不复用 TagEditor：TagEditor 的语义是「改某个素材的标签」
 * （标记增删后保存提交），这里只是「攒一组查询条件」，交互不同。
 * 相同点是性能约束：标签库有 2 万+，必须虚拟化 + 默认限量 + 搜索（见 CLAUDE.md）。
 *
 * 审查修复（P1-9）：换 `ui/BottomSheet`（安全区、遮罩可点关闭、键盘避让、无 78% 硬编码高度）。
 */

import { useEffect, useMemo, useState } from 'react'
import { FlatList, Pressable, StyleSheet, TextInput, View } from 'react-native'
import Icon from './Icon'
import AppText from './ui/AppText'
import Button from './ui/Button'
import BottomSheet from './ui/BottomSheet'
import { useStyles, useTheme } from '../hooks/useTheme'
import { tagCategoryLabel } from '../constants/labels'
import type { TagCategoryGroup } from '../services/api'
import type { Theme } from '../constants/theme'

/** 不带搜索词时展示的条数（避免开屏就渲染过多） */
const DEFAULT_LIMIT = 60
/** 带搜索词时的上限 */
const SEARCH_LIMIT = 200

interface Props {
  visible: boolean
  loading: boolean
  groups: TagCategoryGroup[]
  /** 已选标签名 */
  selected: string[]
  onClose: () => void
  /** 确认选择 */
  onConfirm: (names: string[]) => void
}

export default function TagFilterSheet({
  visible,
  loading,
  groups,
  selected,
  onClose,
  onConfirm,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string[]>(selected)

  useEffect(() => {
    if (visible) {
      setQuery('')
      setPicked(selected)
    }
  }, [visible, selected])

  const all = useMemo(() => {
    const flat: { id: number; name: string; category: string }[] = []
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

  const toggle = (name: string) => {
    setPicked((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  return (
    <BottomSheet
      visible={visible}
      title="按标签筛选"
      onClose={onClose}
      testID="tag-filter-sheet"
      footer={
        <>
          <AppText variant="caption" color="textTertiary" style={styles.footerInfo}>
            已选 {picked.length} 个
          </AppText>
          <Button title="清空" variant="secondary" onPress={() => setPicked([])} />
          <Button title="确定" onPress={() => onConfirm(picked)} testID="tag-filter-ok" />
        </>
      }
    >
      <View style={styles.searchRow}>
        <Icon name="search-outline" size={theme.icon.sm} color={theme.color.textTertiary} />
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

      {loading ? (
        <View style={styles.fillBox}>
          <AppText variant="bodySm" color="textSecondary">
            正在加载标签…
          </AppText>
        </View>
      ) : (
        <>
          <AppText variant="caption" color="textTertiary" style={styles.hintLine}>
            {q ? `匹配 ${visibleTags.length} 个` : `共 ${all.length} 个标签，输入关键词缩小范围`}
          </AppText>
          <FlatList
            data={visibleTags}
            keyExtractor={(t) => String(t.id)}
            style={styles.list}
            keyboardShouldPersistTaps="handled"
            initialNumToRender={14}
            windowSize={6}
            removeClippedSubviews
            ListEmptyComponent={
              <AppText variant="bodySm" color="textTertiary" style={styles.empty}>
                没有匹配的标签
              </AppText>
            }
            renderItem={({ item }) => {
              const on = picked.includes(item.name)
              return (
                <Pressable
                  testID={`tag-option-${item.id}`}
                  style={({ pressed }) => [styles.row, pressed && styles.pressed]}
                  onPress={() => toggle(item.name)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: on }}
                >
                  <Icon
                    name={on ? 'checkmark-circle' : 'ellipse-outline'}
                    size={theme.icon.md}
                    color={on ? theme.color.brandText : theme.color.textDisabled}
                  />
                  <AppText
                    variant="body"
                    color={on ? 'brandText' : 'textPrimary'}
                    numberOfLines={1}
                    style={styles.rowName}
                  >
                    {item.name}
                  </AppText>
                  <AppText variant="caption" color="textTertiary">
                    {tagCategoryLabel(item.category)}
                  </AppText>
                </Pressable>
              )
            }}
          />
        </>
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
      backgroundColor: t.color.bgSubtle,
    },
    searchInput: { flex: 1, fontSize: t.font.body.fontSize, color: t.color.textPrimary },
    fillBox: { paddingVertical: t.space.xxxl, alignItems: 'center' },
    hintLine: { paddingHorizontal: t.space.lg, paddingTop: t.space.sm },
    list: { maxHeight: 360, marginTop: t.space.xs },
    row: {
      minHeight: 48,
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
    },
    rowName: { flex: 1 },
    empty: { textAlign: 'center', paddingVertical: t.space.xl },
    pressed: { backgroundColor: t.color.bgSubtle },
    footerInfo: { flex: 1 },
  })
