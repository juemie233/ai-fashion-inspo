/** 合集选择器：把选中的素材加进已有合集，或现场新建一个。
 *
 * 只在多选态用（见 docs/移动端.md：手机上不做合集拖拽排序）。
 *
 * 审查修复（P1-9）：换 `ui/BottomSheet`（安全区、遮罩可点关闭、键盘避让、无 62% 硬编码高度）。
 */

import { useEffect, useState } from 'react'
import { FlatList, Pressable, StyleSheet, TextInput, View } from 'react-native'
import Icon from './Icon'
import AppText from './ui/AppText'
import Button from './ui/Button'
import BottomSheet from './ui/BottomSheet'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { CollectionItem } from '../services/api'
import type { Theme } from '../constants/theme'

interface Props {
  visible: boolean
  /** 要加入的素材数量（仅用于文案） */
  count: number
  collections: CollectionItem[]
  loading: boolean
  /** 提交中 */
  saving?: boolean
  onClose: () => void
  /** 选中已有合集 */
  onPick: (collection: CollectionItem) => void
  /** 新建合集 */
  onCreate: (name: string) => void
}

export default function CollectionPicker({
  visible,
  count,
  collections,
  loading,
  saving = false,
  onClose,
  onPick,
  onCreate,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const [name, setName] = useState('')

  useEffect(() => {
    if (visible) setName('')
  }, [visible])

  /** 只列一级手动合集（二级收藏夹和智能合集不作为投放目标，避免误操作） */
  const targets = collections.filter((c) => !c.parent_id && c.kind === 'manual')

  return (
    <BottomSheet
      visible={visible}
      title="加入合集"
      onClose={onClose}
      testID="collection-picker"
      footer={
        <>
          <AppText variant="caption" color="textTertiary" style={styles.footerInfo}>
            已选 {count} 张
          </AppText>
          <Button title="取消" variant="secondary" onPress={onClose} disabled={saving} />
        </>
      }
    >
      <AppText variant="caption" color="textTertiary" style={styles.hint}>
        把选中的 {count} 张素材加入下面某个合集
      </AppText>

      {/* 新建合集 */}
      <View style={styles.createRow}>
        <TextInput
          style={styles.input}
          value={name}
          onChangeText={setName}
          placeholder="新建合集名称"
          placeholderTextColor={theme.color.textTertiary}
          autoCorrect={false}
        />
        <Button
          title="新建并加入"
          onPress={() => onCreate(name.trim())}
          disabled={!name.trim() || saving}
          loading={saving}
        />
      </View>

      {loading ? (
        <AppText variant="bodySm" color="textTertiary" style={styles.empty}>
          正在加载合集…
        </AppText>
      ) : (
        <FlatList
          data={targets}
          keyExtractor={(c) => String(c.id)}
          style={styles.list}
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={
            <AppText variant="bodySm" color="textTertiary" style={styles.empty}>
              还没有合集，可以在上面新建一个
            </AppText>
          }
          renderItem={({ item }) => (
            <Pressable
              style={({ pressed }) => [styles.row, pressed && styles.pressed]}
              onPress={() => onPick(item)}
              disabled={saving}
              accessibilityRole="button"
            >
              <Icon name="albums-outline" size={theme.icon.md} color={theme.color.brandText} />
              <AppText variant="body" numberOfLines={1} style={styles.rowName}>
                {item.name}
              </AppText>
              <AppText variant="caption" color="textTertiary">
                {item.item_count ?? 0} 张
              </AppText>
            </Pressable>
          )}
        />
      )}
    </BottomSheet>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    hint: { paddingHorizontal: t.space.lg, paddingBottom: t.space.sm },
    createRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
      paddingBottom: t.space.sm,
    },
    input: {
      flex: 1,
      minHeight: t.touch,
      borderWidth: 1,
      borderColor: t.color.border,
      borderRadius: t.radius.control,
      paddingHorizontal: t.space.md,
      fontSize: t.font.body.fontSize,
      color: t.color.textPrimary,
    },
    list: { maxHeight: 360 },
    row: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    rowName: { flex: 1 },
    empty: { textAlign: 'center', paddingVertical: t.space.xxl },
    pressed: { backgroundColor: t.color.bgSubtle },
    footerInfo: { flex: 1 },
  })
