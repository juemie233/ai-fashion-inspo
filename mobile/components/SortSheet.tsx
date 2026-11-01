/** 排序弹层：底部单选（勾选当前项）。
 *
 * 审查报告 P1-7：改前点一次「最新」变「最早」，再点「评分」「随机」，
 * 要回到「最新」最多点 4 次，而且没有菜单提示还有哪些选项。
 */

import { Pressable, StyleSheet, View } from 'react-native'
import BottomSheet from './ui/BottomSheet'
import AppText from './ui/AppText'
import Icon from './Icon'
import { SORT_OPTIONS } from '../constants/labels'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'
import type { SortKey } from '../services/api'

interface Props {
  visible: boolean
  value: SortKey
  onClose: () => void
  onPick: (key: SortKey) => void
}

export default function SortSheet({ visible, value, onClose, onPick }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)

  return (
    <BottomSheet visible={visible} title="排序方式" onClose={onClose} testID="sort-sheet">
      {SORT_OPTIONS.map((option) => {
        const active = option.key === value
        return (
          <Pressable
            key={option.key}
            testID={`sort-option-${option.key}`}
            onPress={() => onPick(option.key)}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            style={({ pressed }) => [styles.row, pressed && styles.pressed]}
          >
            <AppText variant="body" color={active ? 'brandText' : 'textPrimary'}>
              {option.label}
            </AppText>
            {active ? (
              <Icon name="checkmark" size={theme.icon.md} color={theme.color.brandText} />
            ) : (
              <View style={styles.spacer} />
            )}
          </Pressable>
        )
      })}
    </BottomSheet>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    row: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: t.space.lg,
    },
    spacer: { width: t.icon.md },
    pressed: { backgroundColor: t.color.bgSubtle },
  })
