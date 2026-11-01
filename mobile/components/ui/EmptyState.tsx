/** 空态：图标 56 + 标题 + 说明 + 可选主按钮。
 *
 * 与 `ErrorState` 分开的原因（审查报告 P1-5）：改前多个页面写
 * `{error || '还没有合集'}`，断网时用户看到的是「本来就没有内容」，
 * 永远等不到重试入口。空态**只**表示「确实没有数据」。
 */

import { StyleSheet, View } from 'react-native'
import AppText from './AppText'
import Button from './Button'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  /** Ionicons 图标名 */
  icon?: string
  title: string
  description?: string
  /** 主按钮文案（如「去采集」） */
  actionLabel?: string
  onAction?: () => void
  testID?: string
}

export default function EmptyState({
  icon = 'images-outline',
  title,
  description,
  actionLabel,
  onAction,
  testID,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  return (
    <View testID={testID} style={styles.container}>
      <Icon name={icon} size={56} color={theme.color.textDisabled} />
      <AppText variant="subtitle" color="textPrimary" style={styles.center}>
        {title}
      </AppText>
      {description ? (
        <AppText variant="caption" color="textTertiary" style={styles.center}>
          {description}
        </AppText>
      ) : null}
      {actionLabel && onAction ? (
        <Button title={actionLabel} onPress={onAction} variant="primary" style={styles.action} />
      ) : null}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: {
      alignItems: 'center',
      justifyContent: 'center',
      gap: t.space.sm,
      paddingVertical: 64,
      paddingHorizontal: t.space.xxl,
    },
    center: { textAlign: 'center' },
    action: { marginTop: t.space.md, minWidth: 132 },
  })
