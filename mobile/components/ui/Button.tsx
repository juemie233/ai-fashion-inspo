/** 按钮：四种语义变体 + loading/disabled，高度固定 ≥ 44。
 *
 * 审查报告 P0-10：设置页曾把「测试连接」（紫）和「保存并刷新」（靛）做成两个
 * 同尺寸实心主按钮，用户分不清主操作。现在一屏只允许一个 `primary`：
 * 主操作用 primary，其余用 secondary / ghost。
 */

import { ActivityIndicator, Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import AppText from './AppText'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

/** 变体 */
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface Props {
  title: string
  onPress?: () => void
  variant?: ButtonVariant
  /** 加载中：显示 spinner 并禁用 */
  loading?: boolean
  disabled?: boolean
  /** 左侧图标名（Ionicons） */
  icon?: string
  /** 占满一行 */
  fullWidth?: boolean
  testID?: string
  style?: StyleProp<ViewStyle>
}

export default function Button({
  title,
  onPress,
  variant = 'primary',
  loading = false,
  disabled = false,
  icon,
  fullWidth = false,
  testID,
  style,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const inactive = disabled || loading

  const textColor =
    variant === 'primary' || variant === 'danger'
      ? 'textOnMedia'
      : variant === 'ghost'
        ? 'brandText'
        : 'textPrimary'
  const iconColor = theme.color[textColor]

  return (
    <Pressable
      testID={testID}
      onPress={inactive ? undefined : onPress}
      disabled={inactive}
      accessibilityRole="button"
      accessibilityState={{ disabled: inactive, busy: loading }}
      style={({ pressed }) => [
        styles.base,
        variant === 'primary' && styles.primary,
        variant === 'secondary' && styles.secondary,
        variant === 'ghost' && styles.ghost,
        variant === 'danger' && styles.danger,
        pressed && !inactive && styles.pressed,
        inactive && styles.inactive,
        fullWidth && styles.fullWidth,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={iconColor} />
      ) : (
        <View style={styles.content}>
          {icon ? <Icon name={icon} size={theme.icon.md} color={iconColor} /> : null}
          <AppText variant="subtitle" color={textColor} numberOfLines={1}>
            {title}
          </AppText>
        </View>
      )}
    </Pressable>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    base: {
      minHeight: t.touch,
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.md,
      borderRadius: t.radius.control,
      alignItems: 'center',
      justifyContent: 'center',
      flexDirection: 'row',
      gap: t.space.sm,
    },
    content: { flexDirection: 'row', alignItems: 'center', gap: t.space.sm },
    primary: { backgroundColor: t.color.brandFill },
    secondary: { backgroundColor: t.color.bgSurface, borderWidth: 1, borderColor: t.color.border },
    ghost: { backgroundColor: 'transparent' },
    danger: { backgroundColor: t.color.dangerFill },
    pressed: { opacity: 0.85 },
    inactive: { opacity: 0.45 },
    fullWidth: { alignSelf: 'stretch' },
  })
