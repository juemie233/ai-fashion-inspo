/** 全局错误边界：把「白屏」变成可读的错误页。
 *
 * 为什么要它：release 包里 JS 抛异常时 React 会卸载整棵树，用户只看到白屏，
 * 没有任何线索（2026-10-23 真机上「点保存后白屏」就是这么丢的信息）。这里把错误
 * 捕获后直接画在屏幕上，同时写一份到 console（`adb logcat` 能看到）。
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  info: string
  /** 错误调用栈（Hermes 下带 index.android.bundle 的行号，用来反查具体代码行） */
  stack: string
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: '', stack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 落一份到原生日志，便于 adb logcat 抓取
    console.error('[ErrorBoundary]', error?.message, error?.stack, info?.componentStack)
    this.setState({ info: info?.componentStack ?? '', stack: error?.stack ?? '' })
  }

  handleRetry = (): void => {
    this.setState({ error: null, info: '', stack: '' })
  }

  render(): ReactNode {
    const { error, info, stack } = this.state
    if (!error) return this.props.children

    return (
      <View style={styles.page}>
        <Text style={styles.title}>应用出错了</Text>
        <Text style={styles.hint}>截图这两段发我，行号能直接定位</Text>
        <ScrollView style={styles.box}>
          <Text style={styles.msg}>{String(error.message || error)}</Text>
          {!!stack && (
            <>
              <Text style={styles.sectionTitle}>调用栈（含 bundle 行号）</Text>
              <Text style={styles.stack}>{stack.slice(0, 900)}</Text>
            </>
          )}
          {!!info && (
            <>
              <Text style={styles.sectionTitle}>组件栈</Text>
              <Text style={styles.stack}>{info.slice(0, 900)}</Text>
            </>
          )}
        </ScrollView>
        <TouchableOpacity style={styles.btn} onPress={this.handleRetry}>
          <Text style={styles.btnText}>重试</Text>
        </TouchableOpacity>
      </View>
    )
  }
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#fff', padding: 20, paddingTop: 60 },
  title: { fontSize: 18, fontWeight: '700', color: '#b91c1c', marginBottom: 6 },
  hint: { fontSize: 12, color: '#6b7280', marginBottom: 12 },
  box: {
    flex: 1,
    backgroundColor: '#f9fafb',
    borderRadius: 8,
    padding: 12,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  msg: { fontSize: 13, color: '#111827', marginBottom: 10 },
  sectionTitle: { fontSize: 12, fontWeight: '600', color: '#374151', marginTop: 8, marginBottom: 4 },
  stack: { fontSize: 11, color: '#6b7280', lineHeight: 16 },
  btn: {
    marginTop: 16,
    backgroundColor: '#6366f1',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  btnText: { color: '#fff', fontSize: 15, fontWeight: '600' },
})
