/** 列表行：设置入口 / 人物 / 合集等的统一行样式（左图形 + 主文本 + 副文本 + 右箭头）。
 *
 * 审查报告 §4.7：列表行统一 56 高、15 主文本、12 副文本、行间 1px 分隔线。
 */

import type { ReactNode } from 'react'
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import AppText from './AppText'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  title: string
  subtitle?: string
  /** 左侧自定义内容（头像图等），优先于 icon */
  leading?: ReactNode
  /** 左侧图标名（Ionicons） */
  icon?: string
  /** 右侧自定义内容（数量、状态等） */
  right?: ReactNode
  onPress?: () => void
  /** 显示右箭头，默认 onPress 存在时显示 */
  showChevron?: boolean
  /** 显示底部 1px 分隔线（最后一行传 false） */
  divider?: boolean
  testID?: string
  style?: StyleProp<ViewStyle>
}

export default function ListRow({
  title,
  subtitle,
  leading,
  icon,
  right,
  onPress,
  showChevron,
  divider = true,
  testID,
  style,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const chevron = showChevron ?? Boolean(onPress)

  const body = (
    <>
      {leading ?? (icon ? <Icon name={icon} size={theme.icon.md} color={theme.color.brandText} /> : null)}
      <View style={styles.texts}>
        <AppText variant="subtitle" numberOfLines={1}>
          {title}
        </AppText>
        {subtitle ? (
          <AppText variant="caption" color="textTertiary" numberOfLines={1}>
            {subtitle}
          </AppText>
        ) : null}
      </View>
      {right}
      {chevron ? (
        <Icon name="chevron-forward" size={theme.icon.sm} color={theme.color.textDisabled} />
      ) : null}
    </>
  )

  return (
    <View style={[divider && styles.divider, style]}>
      {onPress ? (
        <Pressable
          testID={testID}
          onPress={onPress}
          accessibilityRole="button"
          style={({ pressed }) => [styles.row, pressed && styles.pressed]}
        >
          {body}
        </Pressable>
      ) : (
        <View testID={testID} style={styles.row}>
          {body}
        </View>
      )}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    divider: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.color.divider },
    row: {
      minHeight: 56,
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
    },
    texts: { flex: 1, gap: 2 },
    pressed: { backgroundColor: t.color.bgSubtle },
  })
