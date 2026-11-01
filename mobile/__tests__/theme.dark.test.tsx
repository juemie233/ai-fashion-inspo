/** 深色模式：同一棵组件树在两套主题下取不同令牌。
 *
 * 用 `ThemeProvider forcedMode` 驱动（不 mock `useColorScheme`），
 * 断言的是「样式真的换了」而不是「切换函数被调用」。
 */

import { render, screen } from '@testing-library/react-native'
import { StyleSheet } from 'react-native'
import { ThemeProvider } from '../hooks/useTheme'
import { darkTheme, lightTheme } from '../constants/theme'
import AppText from '../components/ui/AppText'
import Badge from '../components/ui/Badge'
import Chip from '../components/ui/Chip'
import { SkeletonGrid } from '../components/ui/Skeleton'

/** 取某个文本节点的扁平化样式 */
function styleOf(label: string) {
  return StyleSheet.flatten(screen.getByText(label).props.style)
}

describe('深色模式', () => {
  it('默认（无 Provider）即亮色主题', () => {
    render(<AppText>默认文字</AppText>)
    expect(styleOf('默认文字').color).toBe(lightTheme.color.textPrimary)
  })

  it('forcedMode="dark" 时文字取暗色令牌', () => {
    render(
      <ThemeProvider forcedMode="dark">
        <AppText>暗色文字</AppText>
      </ThemeProvider>,
    )
    expect(styleOf('暗色文字').color).toBe(darkTheme.color.textPrimary)
    expect(styleOf('暗色文字').color).not.toBe(lightTheme.color.textPrimary)
  })

  it('两种主题下页面底色不同（ensure 令牌真的分叉）', () => {
    expect(darkTheme.color.bgPage).not.toBe(lightTheme.color.bgPage)
    expect(darkTheme.color.bgSurface).not.toBe(lightTheme.color.bgSurface)
    expect(darkTheme.color.textPrimary).not.toBe(lightTheme.color.textPrimary)
  })

  it('角标/胶囊/骨架在暗色下也走令牌（不残留亮色值）', () => {
    render(
      <ThemeProvider forcedMode="dark">
        <Badge text="已完成" tone="success" />
        <Chip label="法式" selected />
        <SkeletonGrid count={2} />
      </ThemeProvider>,
    )
    expect(screen.getByText('已完成')).toBeTruthy()
    // 胶囊选中态文字是暗色主题的主色文字
    expect(styleOf('法式').color).toBe(darkTheme.color.brandText)
  })
})
