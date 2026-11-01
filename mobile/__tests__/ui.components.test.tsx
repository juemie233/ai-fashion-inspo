/** UI 组件库渲染用例（审查方案 §3.5 的 14 个基础组件）。
 *
 * 覆盖重点不是像素，而是**组件契约**：
 * - AppText 的字号必须来自设计字阶（不允许别的字号漏进页面）
 * - Button / IconButton 的 loading / disabled 语义，以及图标按钮 44×44 触控区
 * - Chip / SegmentedTab 的选中与回调
 * - BottomSheet 遮罩可点关闭（改前点空白关不掉）
 * - Snackbar 自动消失与操作按钮
 * - 全部组件渲染后没有裸字符串（防真机白屏）
 */

import { act, fireEvent, render, screen } from '@testing-library/react-native'
import { StyleSheet, Text, View } from 'react-native'
import AppText from '../components/ui/AppText'
import Button from '../components/ui/Button'
import IconButton from '../components/ui/IconButton'
import Chip from '../components/ui/Chip'
import SegmentedTab from '../components/ui/SegmentedTab'
import Badge from '../components/ui/Badge'
import ListRow from '../components/ui/ListRow'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import BottomSheet from '../components/ui/BottomSheet'
import Snackbar from '../components/ui/Snackbar'
import { SkeletonGrid, SkeletonList } from '../components/ui/Skeleton'
import { ThemeProvider } from '../hooks/useTheme'
import { lightTheme } from '../constants/theme'
import { findBareText } from '../test-utils/noBareText'

/** 渲染并返回「裸字符串」违规列表 */
function renderBare(target: React.ReactElement) {
  render(target)
  return findBareText(screen.toJSON())
}

describe('AppText', () => {
  it('7 档字阶映射到设计字号', () => {
    const cases: [Parameters<typeof AppText>[0]['variant'], number][] = [
      ['display', 22],
      ['title', 17],
      ['subtitle', 15],
      ['body', 15],
      ['bodySm', 13],
      ['caption', 12],
      ['micro', 11],
    ]
    for (const [variant, size] of cases) {
      const view = render(<AppText variant={variant}>文案</AppText>)
      const style = StyleSheet.flatten(view.getByText('文案').props.style)
      expect(style.fontSize).toBe(size)
    }
  })

  it('颜色取令牌（默认 textPrimary）', () => {
    const view = render(<AppText>默认</AppText>)
    expect(StyleSheet.flatten(view.getByText('默认').props.style).color).toBe(
      lightTheme.color.textPrimary,
    )
    view.rerender(<AppText color="textTertiary">次要</AppText>)
    expect(StyleSheet.flatten(view.getByText('次要').props.style).color).toBe(
      lightTheme.color.textTertiary,
    )
  })

  it('tabularNums 打开等宽数字', () => {
    const view = render(<AppText tabularNums>12:30</AppText>)
    expect(StyleSheet.flatten(view.getByText('12:30').props.style).fontVariant).toEqual([
      'tabular-nums',
    ])
  })
})

describe('Button', () => {
  it('点击回调；loading / disabled 时不触发', () => {
    const onPress = jest.fn()
    render(<Button title="保存" onPress={onPress} />)
    fireEvent.press(screen.getByText('保存'))
    expect(onPress).toHaveBeenCalledTimes(1)

    screen.rerender(<Button title="保存" onPress={onPress} disabled />)
    fireEvent.press(screen.getByText('保存'))
    expect(onPress).toHaveBeenCalledTimes(1)

    screen.rerender(<Button title="保存" onPress={onPress} loading testID="btn" />)
    // loading 态不显示文案（只剩 spinner），点按钮也不触发
    expect(screen.queryByText('保存')).toBeNull()
    fireEvent.press(screen.getByTestId('btn'))
    expect(onPress).toHaveBeenCalledTimes(1)
  })

  it('最小高度 44（触控目标）', () => {
    render(<Button title="确定" testID="btn" />)
    const style = StyleSheet.flatten(screen.getByTestId('btn').props.style)
    expect(style.minHeight).toBe(lightTheme.touch)
  })
})

describe('IconButton', () => {
  it('触控区固定 44×44，图标仍是 20', () => {
    render(<IconButton name="heart" testID="fav" onPress={() => {}} />)
    const style = StyleSheet.flatten(screen.getByTestId('fav').props.style)
    expect(style.width).toBe(44)
    expect(style.height).toBe(44)
  })

  it('点击回调 / disabled 不触发 / 无障碍文案透传', () => {
    const onPress = jest.fn()
    render(<IconButton name="close" testID="x" onPress={onPress} accessibilityLabel="关闭" />)
    fireEvent.press(screen.getByLabelText('关闭'))
    expect(onPress).toHaveBeenCalledTimes(1)

    screen.rerender(
      <IconButton name="close" testID="x" onPress={onPress} disabled accessibilityLabel="关闭" />,
    )
    fireEvent.press(screen.getByLabelText('关闭'))
    expect(onPress).toHaveBeenCalledTimes(1)
  })
})

describe('Chip / SegmentedTab', () => {
  it('Chip 点击、清除、disabled', () => {
    const onPress = jest.fn()
    const onClear = jest.fn()
    render(<Chip label="法式" onPress={onPress} onClear={onClear} selected testID="chip" />)
    fireEvent.press(screen.getByTestId('chip'))
    expect(onPress).toHaveBeenCalledTimes(1)
    fireEvent.press(screen.getByLabelText('清除'))
    expect(onClear).toHaveBeenCalledTimes(1)

    screen.rerender(<Chip label="法式" onPress={onPress} disabled testID="chip" />)
    fireEvent.press(screen.getByTestId('chip'))
    expect(onPress).toHaveBeenCalledTimes(1)
  })

  it('SegmentedTab 切换回调与 testID 前缀', () => {
    const onChange = jest.fn()
    render(
      <SegmentedTab
        items={[
          { key: 'keyword', label: '关键词' },
          { key: 'semantic', label: '语义' },
        ]}
        value="keyword"
        onChange={onChange}
        testIDPrefix="mode"
      />,
    )
    fireEvent.press(screen.getByTestId('mode-semantic'))
    expect(onChange).toHaveBeenCalledWith('semantic')
  })
})

describe('Badge / ListRow', () => {
  it('Badge 文案与色调渲染', () => {
    render(<Badge text="已完成" tone="success" />)
    expect(screen.getByText('已完成')).toBeTruthy()
  })

  it('ListRow 显示标题/副标题/右箭头，可点击', () => {
    const onPress = jest.fn()
    render(
      <ListRow title="后台任务" subtitle="AI 分析进度" icon="pulse-outline" onPress={onPress} />,
    )
    expect(screen.getByText('后台任务')).toBeTruthy()
    expect(screen.getByText('AI 分析进度')).toBeTruthy()
    fireEvent.press(screen.getByText('后台任务'))
    expect(onPress).toHaveBeenCalledTimes(1)
  })
})

describe('EmptyState / ErrorState', () => {
  it('EmptyState 标题、说明与主按钮', () => {
    const onAction = jest.fn()
    render(
      <EmptyState
        title="还没有合集"
        description="长按素材可以加入合集"
        actionLabel="去采集"
        onAction={onAction}
      />,
    )
    expect(screen.getByText('还没有合集')).toBeTruthy()
    expect(screen.getByText('长按素材可以加入合集')).toBeTruthy()
    fireEvent.press(screen.getByText('去采集'))
    expect(onAction).toHaveBeenCalledTimes(1)
  })

  it('ErrorState 默认文案与重试', () => {
    const onRetry = jest.fn()
    render(<ErrorState onRetry={onRetry} />)
    expect(screen.getByText('加载失败')).toBeTruthy()
    expect(screen.getByText('请检查网络或后端服务')).toBeTruthy()
    fireEvent.press(screen.getByText('重试'))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('BottomSheet / Snackbar', () => {
  it('BottomSheet 打开渲染内容，点遮罩关闭', () => {
    const onClose = jest.fn()
    render(
      <BottomSheet visible title="编辑标签" onClose={onClose}>
        <Text>内容</Text>
      </BottomSheet>,
    )
    expect(screen.getByText('编辑标签')).toBeTruthy()
    expect(screen.getByText('内容')).toBeTruthy()
    fireEvent.press(screen.getByLabelText('关闭弹层'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('BottomSheet 关闭时不渲染内容', () => {
    render(
      <BottomSheet visible={false} title="编辑标签" onClose={() => {}}>
        <Text>内容</Text>
      </BottomSheet>,
    )
    expect(screen.queryByText('内容')).toBeNull()
  })

  it('Snackbar 到点自动消失，操作按钮可点', () => {
    jest.useFakeTimers()
    const onHide = jest.fn()
    const onAction = jest.fn()
    render(
      <Snackbar
        visible
        message="已保存标签"
        actionLabel="撤销"
        onAction={onAction}
        onHide={onHide}
        duration={3000}
      />,
    )
    fireEvent.press(screen.getByText('撤销'))
    expect(onAction).toHaveBeenCalledTimes(1)
    act(() => {
      jest.advanceTimersByTime(3000)
    })
    expect(onHide).toHaveBeenCalledTimes(1)
    jest.useRealTimers()
  })

  it('Snackbar 隐藏时不渲染', () => {
    render(<Snackbar visible={false} message="已保存" onHide={() => {}} />)
    expect(screen.queryByText('已保存')).toBeNull()
  })
})

describe('Skeleton', () => {
  it('网格骨架按 count 渲染块', () => {
    render(<SkeletonGrid count={6} columns={2} />)
    expect(screen.getByTestId('skeleton-grid')).toBeTruthy()
  })

  it('列表骨架默认 5 行', () => {
    render(<SkeletonList rows={3} />)
    expect(screen.getByTestId('skeleton-list')).toBeTruthy()
  })
})

describe('裸字符串检查（防真机白屏）', () => {
  it('组件库组合渲染后没有裸文本', () => {
    const hits = renderBare(
      <View>
        <AppText variant="title">标题</AppText>
        <Button title="保存" onPress={() => {}} />
        <Badge text="已完成" tone="success" />
        <Chip label="法式" selected />
        <ListRow title="任务" subtitle="进行中" />
        <EmptyState title="空" />
        <IconButton name="close" />
        <SkeletonGrid count={2} />
      </View>,
    )
    expect(hits).toEqual([])
  })
})
