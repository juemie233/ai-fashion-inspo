/** 错误态：图标 + 标题 + 说明 + 重试按钮（必须给重试）。
 *
 * 用法约定：列表页 `error && items.length === 0` 时渲染本组件；
 * 已有数据时的刷新失败走 `Snackbar`/行内提示，不要把整页换成错误页。
 */

import { StyleSheet, View } from 'react-native'
import AppText from './AppText'
import Button from './Button'
import Icon from '../Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  title?: string
  description?: string
  /** 重试回调 */
  onRetry: () => void
  testID?: string
}

export default function ErrorState({
  title = '加载失败',
  description = '请检查网络或后端服务',
  onRetry,
  testID,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  return (
    <View testID={testID} style={styles.container}>
      <Icon name="cloud-offline-outline" size={56} color={theme.color.textDisabled} />
      <AppText variant="subtitle" color="textPrimary" style={styles.center}>
        {title}
      </AppText>
      <AppText variant="caption" color="textTertiary" style={styles.center}>
        {description}
      </AppText>
      <Button
        title="重试"
        icon="refresh"
        variant="secondary"
        onPress={onRetry}
        style={styles.action}
        testID="retry-button"
      />
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
