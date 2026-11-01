/** 主题注入：把 `constants/theme.ts` 的令牌送到组件树里。
 *
 * 用法（页面/组件的样式全部走这一套，禁止再写死颜色）：
 * ```tsx
 * const styles = useStyles(makeStyles)
 * const makeStyles = (t: Theme) => StyleSheet.create({ ... })
 * ```
 * `makeStyles` 必须定义在模块顶层（引用稳定），`useStyles` 才能按主题缓存。
 *
 * 为什么不是「直接 import 令牌」：暗色模式要求同一份组件渲染两套颜色，
 * 模块级 `StyleSheet.create` 会把颜色在模块加载时定死，切主题不会重算。
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useColorScheme } from 'react-native'
import { darkTheme, lightTheme, type Theme } from '../constants/theme'

/** 默认亮色：组件在测试里被单独渲染（没有 Provider）时也不会崩 */
const ThemeContext = createContext<Theme>(lightTheme)

interface Props {
  children: ReactNode
  /** 强制主题，供测试/截图核对用；不传则跟随系统 */
  forcedMode?: 'light' | 'dark'
}

/** 主题 Provider：跟随系统深浅色（app.json 的 userInterfaceStyle 决定系统是否上报暗色） */
export function ThemeProvider({ children, forcedMode }: Props) {
  const scheme = useColorScheme()
  const mode = forcedMode ?? (scheme === 'dark' ? 'dark' : 'light')
  const theme = mode === 'dark' ? darkTheme : lightTheme
  return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
}

/** 取当前主题令牌 */
export function useTheme(): Theme {
  return useContext(ThemeContext)
}

/** 按主题生成样式表（`factory` 必须是模块级稳定引用） */
export function useStyles<T>(factory: (theme: Theme) => T): T {
  const theme = useTheme()
  return useMemo(() => factory(theme), [factory, theme])
}
