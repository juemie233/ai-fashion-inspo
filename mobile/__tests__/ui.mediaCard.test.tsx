/** MediaCard：图片卡规格（标签折叠、角标、收藏、多选态、点击/长按）。
 *
 * 这些是审查报告 P0-6 / P1-3 的验收点：同一张卡在五个页面共用一套规格，
 * 标签最多 2 个 + `+N`，视频角标不与标签重叠，收藏按钮有 44 触控区。
 */

import { fireEvent, render, screen } from '@testing-library/react-native'
import { StyleSheet, View } from 'react-native'
import MediaCard from '../components/ui/MediaCard'
import { findBareText } from '../test-utils/noBareText'

describe('MediaCard', () => {
  it('渲染来源角标与最多 2 个标签，其余折叠成 +N', () => {
    const utils = render(
      <MediaCard
        testID="card-1"
        uri="http://h/a.jpg"
        tags={['法式', '通勤', '白裙', '夏日']}
        badgeText="小红书"
      />,
    )

    expect(screen.getByText('小红书')).toBeTruthy()
    expect(screen.getByText('法式')).toBeTruthy()
    expect(screen.getByText('通勤')).toBeTruthy()
    expect(screen.queryByText('白裙')).toBeNull()
    expect(screen.getByText('+2')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('视频素材显示「视频」角标，图片没有', () => {
    const { rerender } = render(<MediaCard uri="http://h/a.jpg" video />)
    expect(screen.getByText('视频')).toBeTruthy()

    rerender(<MediaCard uri="http://h/a.jpg" />)
    expect(screen.queryByText('视频')).toBeNull()
  })

  it('收藏按钮可点，且触控区 44×44', () => {
    const onToggleFavorite = jest.fn()
    render(
      <MediaCard
        uri="http://h/a.jpg"
        showFavorite
        favorite={false}
        onToggleFavorite={onToggleFavorite}
      />,
    )

    fireEvent.press(screen.getByLabelText('收藏'))
    expect(onToggleFavorite).toHaveBeenCalledTimes(1)
    // 触控区 44×44 由 ui/IconButton 统一保证（断言在 ui.components.test.tsx）
  })

  it('已收藏时朗读文案是「取消收藏」', () => {
    render(<MediaCard uri="http://h/a.jpg" showFavorite favorite />)
    expect(screen.getByLabelText('取消收藏')).toBeTruthy()
  })

  it('多选态不显示收藏按钮，改为勾选标记 + 主色描边', () => {
    render(<MediaCard testID="card-1" uri="http://h/a.jpg" showFavorite selected />)

    expect(screen.queryByLabelText('收藏')).toBeNull()
    const style = StyleSheet.flatten(screen.getByTestId('card-1').props.style)
    expect(style.borderWidth).toBe(2)
  })

  it('点击/长按分别回调', () => {
    const onPress = jest.fn()
    const onLongPress = jest.fn()
    render(
      <MediaCard testID="card-1" uri="http://h/a.jpg" onPress={onPress} onLongPress={onLongPress} />,
    )

    fireEvent.press(screen.getByTestId('card-1'))
    fireEvent(screen.getByTestId('card-1'), 'longPress')
    expect(onPress).toHaveBeenCalledTimes(1)
    expect(onLongPress).toHaveBeenCalledTimes(1)
  })

  it('比例由 props 决定（默认 3:4，合集封面 1:1）', () => {
    render(
      <View>
        <MediaCard testID="grid" uri="http://h/a.jpg" />
        <MediaCard testID="square" uri="http://h/a.jpg" ratio={1} />
      </View>,
    )
    expect(StyleSheet.flatten(screen.getByTestId('grid').props.style).aspectRatio).toBe(0.75)
    expect(StyleSheet.flatten(screen.getByTestId('square').props.style).aspectRatio).toBe(1)
  })

  it('没有标签时不渲染 +N', () => {
    render(<MediaCard uri="http://h/a.jpg" tags={[]} />)
    expect(screen.queryByText(/^\+\d+$/)).toBeNull()
  })
})
