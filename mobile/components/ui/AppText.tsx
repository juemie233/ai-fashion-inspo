/** 文字组件：按设计令牌的 7 档字阶渲染，禁止在页面里再写 `fontSize`。
 *
 * 审查报告 §3.2：改造前全仓散落 11 种字号（9~20），9/10px 共 6 处低于可读下限。
 * 现在字号只有一个来源（`theme.font`），最小 11px。
 */

import { Text, type StyleProp, type TextProps, type TextStyle } from 'react-native'
import { useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

/** 字阶变体 */
export type TextVariant = 'display' | 'title' | 'subtitle' | 'body' | 'bodySm' | 'caption' | 'micro'

/** 颜色令牌名 */
export type TextColorKey = keyof Theme['color']

interface Props extends TextProps {
  /** 字阶，默认 body */
  variant?: TextVariant
  /** 颜色令牌，默认 textPrimary */
  color?: TextColorKey
  /** 数字/时间列用等宽数字，避免跳动 */
  tabularNums?: boolean
  style?: StyleProp<TextStyle>
}

export default function AppText({
  variant = 'body',
  color = 'textPrimary',
  tabularNums = false,
  style,
  ...rest
}: Props) {
  const theme = useTheme()
  const font = theme.font[variant]
  return (
    <Text
      {...rest}
      style={[
        { fontSize: font.fontSize, lineHeight: font.lineHeight, fontWeight: font.fontWeight },
        { color: theme.color[color] },
        tabularNums ? { fontVariant: ['tabular-nums'] } : null,
        style,
      ]}
    />
  )
}
