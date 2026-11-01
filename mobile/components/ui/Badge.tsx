/** 角标：来源 / 视频 / 任务状态 / 智能合集。
 *
 * 色调都是**实心底 + 白字**，保证每种状态的小字号（11px）都过 4.5:1
 * （具体比值由 `__tests__/theme.tokens.test.ts` 计算校验，品牌/成功/警告/危险四色均达标）。
 * 图片上的角标用 `onMedia`（半透明深底 + 白字）。
 */

import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import AppText from './AppText'
import { useStyles } from '../../hooks/useTheme'
import type { StatusTone, Theme } from '../../constants/theme'

interface Props {
  text: string
  tone?: StatusTone
  testID?: string
  style?: StyleProp<ViewStyle>
}

export default function Badge({ text, tone = 'neutral', testID, style }: Props) {
  const styles = useStyles(makeStyles)
  return (
    <View
      testID={testID}
      style={[
        styles.base,
        tone === 'brand' && styles.brand,
        tone === 'success' && styles.success,
        tone === 'warning' && styles.warning,
        tone === 'danger' && styles.danger,
        tone === 'onMedia' && styles.onMedia,
        style,
      ]}
    >
      <AppText
        variant="micro"
        color={tone === 'neutral' ? 'textSecondary' : 'textOnMedia'}
        numberOfLines={1}
      >
        {text}
      </AppText>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    base: {
      paddingHorizontal: t.space.sm,
      paddingVertical: 2,
      borderRadius: t.radius.badge,
      backgroundColor: t.color.bgSubtle,
      alignSelf: 'flex-start',
      maxWidth: 140,
    },
    brand: { backgroundColor: t.color.brandFill },
    success: { backgroundColor: t.color.successFill },
    warning: { backgroundColor: t.color.warningFill },
    danger: { backgroundColor: t.color.dangerFill },
    onMedia: { backgroundColor: t.color.scrim },
  })
