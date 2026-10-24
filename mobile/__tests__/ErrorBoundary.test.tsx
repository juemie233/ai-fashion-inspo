/** 错误边界：子组件抛错时显示可读错误页，而不是白屏。 */

import { render, screen } from '@testing-library/react-native'
import { Text } from 'react-native'
import ErrorBoundary from '../components/ErrorBoundary'

function Boom(): React.ReactElement {
  throw new Error('渲染炸了')
}

describe('ErrorBoundary', () => {
  it('正常时渲染子组件', () => {
    render(
      <ErrorBoundary>
        <Text>子组件内容</Text>
      </ErrorBoundary>,
    )
    expect(screen.getByText('子组件内容')).toBeTruthy()
  })

  it('子组件抛错时显示错误页而不是白屏', () => {
    // React 会把 render 期异常打到 console.error，这里静音避免噪音
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByText('应用出错了')).toBeTruthy()
    expect(screen.getByText('渲染炸了')).toBeTruthy()
    spy.mockRestore()
  })
})
