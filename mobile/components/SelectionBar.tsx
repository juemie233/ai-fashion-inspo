/** 多选态的底部操作栏：收藏 / 加合集 / 移入垃圾桶 / 退出多选。
 *
 * 审查修复：安全区（`insets.bottom`）、删除项红色、三项等宽（§4.2）。
 */

import { Pressable, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Icon from './Icon'
import AppText from './ui/AppText'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'

interface Props {
  /** 已选数量 */
  count: number
  /** 其中已收藏的数量，用于决定「收藏」按钮是收藏还是取消收藏 */
  favoriteCount: number
  busy?: boolean
  onToggleFavorite: () => void
  onAddToCollection: () => void
  onTrash: () => void
  onCancel: () => void
}

export default function SelectionBar({
  count,
  favoriteCount,
  busy = false,
  onToggleFavorite,
  onAddToCollection,
  onTrash,
  onCancel,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const insets = useSafeAreaInsets()
  // 全部已收藏时按钮变成「取消收藏」，否则「收藏」
  const allFavorite = count > 0 && favoriteCount === count

  return (
    <View style={[styles.bar, { paddingBottom: insets.bottom + 8 }]}>
      <View style={styles.info}>
        <AppText variant="bodySm" color="textSecondary">
          已选 {count} 张
        </AppText>
        <Pressable onPress={onCancel} hitSlop={10} accessibilityRole="button">
          <AppText variant="bodySm" color="textTertiary">
            退出多选
          </AppText>
        </Pressable>
      </View>

      <View style={styles.actions}>
        <Pressable
          testID="batch-favorite"
          style={styles.action}
          onPress={onToggleFavorite}
          disabled={busy}
          accessibilityRole="button"
        >
          <Icon
            name={allFavorite ? 'heart-dislike-outline' : 'heart-outline'}
            size={theme.icon.lg}
            color={theme.color.brandText}
          />
          <AppText variant="caption" color="brandText">
            {allFavorite ? '取消收藏' : '收藏'}
          </AppText>
        </Pressable>

        <Pressable
          testID="batch-collection"
          style={styles.action}
          onPress={onAddToCollection}
          disabled={busy}
          accessibilityRole="button"
        >
          <Icon name="albums-outline" size={theme.icon.lg} color={theme.color.brandText} />
          <AppText variant="caption" color="brandText">
            加合集
          </AppText>
        </Pressable>

        <Pressable
          testID="batch-trash"
          style={styles.action}
          onPress={onTrash}
          disabled={busy}
          accessibilityRole="button"
        >
          <Icon name="trash-outline" size={theme.icon.lg} color={theme.color.dangerText} />
          <AppText variant="caption" color="dangerText">
            移入垃圾桶
          </AppText>
        </Pressable>
      </View>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    bar: {
      backgroundColor: t.color.bgSurface,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.border,
    },
    info: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
    },
    actions: { flexDirection: 'row', paddingHorizontal: t.space.sm },
    action: { flex: 1, alignItems: 'center', gap: 2, paddingVertical: t.space.xs },
  })
