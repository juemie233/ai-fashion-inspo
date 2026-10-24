/** 图标组件：直接用内嵌字体渲染 Ionicons 字形。
 *
 * 为什么不用 `<Icon>`：
 * @expo/vector-icons 的组件挂载时会判断 `Font.isLoaded('ionicons')`，而 expo-font
 * 只认自己 loadAsync 过的字体 —— 随包内嵌的字体不算，于是组件直接渲染空 `<Text />`，
 * 表现为「所有图标都不显示」（2026-10-24 真机排查，多条方案验证后确认）。
 *
 * 这里跳过那层判断：字体已由 app.json 的 expo-font plugin 内嵌为
 * `assets/fonts/Ionicons.ttf`（原生字体家族名 = 文件名 `Ionicons`，已实测能渲染），
 * 组件只负责把图标名翻译成码点再画出来，行为可预测。
 *
 * 用法与 `<Icon name size color>` 完全一致。
 */

import { Text, type StyleProp, type TextStyle } from 'react-native'
// 只借用它的 glyphMap（图标名 → 码点），不渲染它的组件
import { Ionicons } from '@expo/vector-icons'

/** 内嵌字体的家族名（= android assets 里的文件名，不含扩展名） */
const FONT_FAMILY = 'Ionicons'

interface Props {
  /** Ionicons 图标名，如 'heart'、'search-outline' */
  name: string
  /** 字号（同时决定图标大小） */
  size?: number
  /** 颜色 */
  color?: string
  style?: StyleProp<TextStyle>
}

export default function Icon({ name, size = 22, color = '#111827', style }: Props) {
  const glyphMap = (Ionicons as unknown as { glyphMap?: Record<string, number> }).glyphMap
  const code = glyphMap?.[name]

  // 名字写错时给一个方块占位，避免「什么都不显示」难以排查
  const glyph = code === undefined ? '?' : String.fromCodePoint(code)

  return (
    <Text
      selectable={false}
      allowFontScaling={false}
      style={[{ fontFamily: FONT_FAMILY, fontSize: size, color, lineHeight: size * 1.15 }, style]}
    >
      {glyph}
    </Text>
  )
}
