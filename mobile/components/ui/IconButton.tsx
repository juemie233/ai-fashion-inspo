/** 图标按钮：**触控容器固定 44×44**（视觉图标可以更小）。
 *
 * 审查报告 P0-5：改前收藏按钮 26/28px、采集移除按钮 20px、排序按钮无 hitSlop，
 * 卡片上的收藏还紧贴图片边缘，误触会直接改变收藏状态。
 * 这里把「可点区域」与「可见图标」分开：容器 44，图标默认 20。
 */

import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

/** 可见背景 */
export type IconButtonBackground = 'none' | 'scrim' | 'surface'

interface Props {
  /** Ionicons 图标名 */
  name: string
  onPress?: () => void
  /** 图标尺寸，默认 20 */
  size?: number
  /** 图标颜色（十六进制或令牌值，由调用方从 theme 取） */
  color?: string
  /** 可见圆形底：图片上用 scrim，白底页面上用 surface */
  background?: IconButtonBackground
  disabled?: boolean
  testID?: string
  /** 朗读文案（无障碍用户依赖；新按钮建议都传） */
  accessibilityLabel?: string
  style?: StyleProp<ViewStyle>
}

export default function IconButton({
  name,
  onPress,
  size,
  color,
  background = 'none',
  disabled = false,
  testID,
  accessibilityLabel,
  style,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const iconSize = size ?? theme.icon.md

  return (
    <Pressable
      testID={testID}
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      style={({ pressed }) => [styles.hit, pressed && !disabled && styles.pressed, style]}
    >
      <View
        style={[
          styles.bubble,
          background === 'scrim' && styles.scrim,
          background === 'surface' && styles.surface,
          disabled && styles.disabled,
        ]}
      >
        <Icon name={name} size={iconSize} color={color ?? theme.color.textPrimary} />
      </View>
    </Pressable>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    // 最小触控区：44×44（theme.touch）
    hit: {
      width: t.touch,
      height: t.touch,
      alignItems: 'center',
      justifyContent: 'center',
    },
    bubble: {
      width: 32,
      height: 32,
      borderRadius: t.radius.pill,
      alignItems: 'center',
      justifyContent: 'center',
    },
    scrim: { backgroundColor: t.color.scrim },
    surface: { backgroundColor: t.color.bgSurface },
    pressed: { opacity: 0.7 },
    disabled: { opacity: 0.4 },
  })
