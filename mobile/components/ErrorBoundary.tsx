/** 全局错误边界：把「白屏」变成可读的错误页。
 *
 * 为什么要它：release 包里 JS 抛异常时 React 会卸载整棵树，用户只看到白屏，
 * 没有任何线索（2026-10-23 真机上「点保存后白屏」就是这么丢的信息）。这里把错误
 * 捕获后直接画在屏幕上，同时写一份到前端日志缓冲（utils/logger），并提供
 * 「分享诊断信息」把平台/版本 + 最近日志通过系统分享面板发给开发者（P2-4）。
 *
 * 主题说明：错误边界挂在 `ThemeProvider` **外面**（要能兜住 Provider 自身的崩溃），
 * 所以这里只能拿到 context 的默认值（亮色令牌）；暗色下错误页仍是亮底，
 * 属于有意取舍——错误页要的是可读，不是观感。
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { ScrollView, Share, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import { useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'
import { buildDiagnostics, logger } from '../utils/logger'

interface Props {
  children: ReactNode
  theme: Theme
}

interface State {
  error: Error | null
  info: string
  /** 错误调用栈（Hermes 下带 index.android.bundle 的行号，用来反查具体代码行） */
  stack: string
}

/** 内部实现：类组件才能实现 componentDidCatch */
class ErrorBoundaryInner extends Component<Props, State> {
  state: State = { error: null, info: '', stack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 写入前端环形缓冲（同时打到控制台，adb logcat 可抓）
    logger.error('[ErrorBoundary] 捕获渲染错误', {
      message: error?.message,
      stack: error?.stack,
      componentStack: info?.componentStack,
    })
    this.setState({ info: info?.componentStack ?? '', stack: error?.stack ?? '' })
  }

  handleRetry = (): void => {
    this.setState({ error: null, info: '', stack: '' })
  }

  /** 分享诊断信息：系统分享面板（微信/邮件/文件传输），不依赖剪贴板权限 */
  handleShareDiagnostics = async (): Promise<void> => {
    try {
      await Share.share({ message: buildDiagnostics(), title: '穿搭素材库诊断信息' })
    } catch {
      // 用户取消分享或系统不支持时忽略：错误页本身仍可截图
    }
  }

  render(): ReactNode {
    const { error, info, stack } = this.state
    const { theme } = this.props
    if (!error) return this.props.children

    const styles = makeStyles(theme)

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
        <View style={styles.btnRow}>
          <TouchableOpacity
            style={styles.btn}
            onPress={this.handleRetry}
            accessibilityRole="button"
            accessibilityLabel="重试"
          >
            <Text style={styles.btnText}>重试</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.btnSecondary}
            onPress={this.handleShareDiagnostics}
            accessibilityRole="button"
            accessibilityLabel="分享诊断信息"
          >
            <Text style={styles.btnSecondaryText}>分享诊断信息</Text>
          </TouchableOpacity>
        </View>
      </View>
    )
  }
}

/** 对外组件：注入主题令牌（未包 Provider 时取 context 默认的亮色主题） */
export default function ErrorBoundary({ children }: { children: ReactNode }) {
  const theme = useTheme()
  return <ErrorBoundaryInner theme={theme}>{children}</ErrorBoundaryInner>
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    page: { flex: 1, backgroundColor: t.color.bgSurface, padding: t.space.xl, paddingTop: 60 },
    title: {
      fontSize: t.font.title.fontSize,
      lineHeight: t.font.title.lineHeight,
      fontWeight: '600',
      color: t.color.dangerText,
      marginBottom: 6,
    },
    hint: {
      fontSize: t.font.caption.fontSize,
      lineHeight: t.font.caption.lineHeight,
      color: t.color.textTertiary,
      marginBottom: t.space.md,
    },
    box: {
      flex: 1,
      backgroundColor: t.color.bgSubtle,
      borderRadius: t.radius.control,
      padding: t.space.md,
      borderWidth: 1,
      borderColor: t.color.border,
    },
    msg: {
      fontSize: t.font.bodySm.fontSize,
      lineHeight: t.font.bodySm.lineHeight,
      color: t.color.textPrimary,
      marginBottom: t.space.sm,
    },
    sectionTitle: {
      fontSize: t.font.caption.fontSize,
      fontWeight: '600',
      color: t.color.textSecondary,
      marginTop: t.space.sm,
      marginBottom: t.space.xs,
    },
    stack: {
      fontSize: t.font.micro.fontSize,
      lineHeight: t.font.micro.lineHeight,
      color: t.color.textTertiary,
    },
    btnRow: {
      marginTop: t.space.lg,
      flexDirection: 'row',
      gap: t.space.sm,
    },
    btn: {
      flex: 1,
      backgroundColor: t.color.brandFill,
      borderRadius: t.radius.control,
      minHeight: t.touch,
      alignItems: 'center',
      justifyContent: 'center',
    },
    btnSecondary: {
      flex: 1,
      backgroundColor: t.color.bgSubtle,
      borderWidth: 1,
      borderColor: t.color.border,
      borderRadius: t.radius.control,
      minHeight: t.touch,
      alignItems: 'center',
      justifyContent: 'center',
    },
    btnText: {
      color: t.color.textOnMedia,
      fontSize: t.font.subtitle.fontSize,
      fontWeight: '600',
    },
    btnSecondaryText: {
      color: t.color.textPrimary,
      fontSize: t.font.subtitle.fontSize,
      fontWeight: '600',
    },
  })
