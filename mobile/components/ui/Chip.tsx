/** 胶囊：筛选条件 / 检索模式 / 已选标签都用它。
 *
 * 审查报告 P1-4：改前「素材库筛选」「搜索模式」「人物 Tab」三个胶囊长得一模一样，
 * 用户分不清是筛选条件还是页面切换。现在两种变体：
 * - `filter`（筛选/模式）：圆角胶囊，选中 = 浅主色底 + 主色描边 + 主色文字
 * - 页面级切换用 `SegmentedTab`（分段控件），不再用胶囊
 */

import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import AppText from './AppText'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  label: string
  selected?: boolean
  onPress?: () => void
  /** 左侧图标 */
  icon?: string
  /** 右侧清除按钮（已选标签的 ×） */
  onClear?: () => void
  disabled?: boolean
  testID?: string
  style?: StyleProp<ViewStyle>
}

export default function Chip({
  label,
  selected = false,
  onPress,
  icon,
  onClear,
  disabled = false,
  testID,
  style,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  // 未选中文字用 textSecondary：textTertiary 压 bgSubtle 只有 4.39:1，不达 AA
  const textColor = selected ? 'brandText' : 'textSecondary'
  const iconColor = selected ? theme.color.brandText : theme.color.textSecondary

  return (
    <Pressable
      testID={testID}
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ selected, disabled }}
      // 胶囊本身高度 34，补上下各 6 让它接近 44 的触控高度
      hitSlop={{ top: 6, bottom: 6, left: 2, right: 2 }}
      style={({ pressed }) => [
        styles.base,
        selected && styles.selected,
        disabled && styles.disabled,
        pressed && !disabled && styles.pressed,
        style,
      ]}
    >
      {icon ? <Icon name={icon} size={theme.icon.sm} color={iconColor} /> : null}
      <AppText variant="bodySm" color={textColor} numberOfLines={1}>
        {label}
      </AppText>
      {onClear ? (
        <Pressable
          onPress={onClear}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="清除"
        >
          <Icon name="close" size={theme.icon.sm} color={iconColor} />
        </Pressable>
      ) : null}
    </Pressable>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    base: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.xs,
      minHeight: 34,
      paddingHorizontal: t.space.md,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.bgSubtle,
      borderWidth: 1,
      borderColor: 'transparent',
      maxWidth: 240,
    },
    selected: { backgroundColor: t.color.brandSubtle, borderColor: t.color.brandText },
    pressed: { opacity: 0.75 },
    disabled: { opacity: 0.45 },
  })
