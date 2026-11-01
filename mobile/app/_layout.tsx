/** 根布局：配置 Expo Router 导航栈 + 主题。
 *
 * 图标说明：图标统一走 `components/Icon.tsx`（直接用内嵌字体渲染字形），
 * 不要再用 `<Ionicons>` —— 它会因 `Font.isLoaded('ionicons')` 判假而渲染空文本，
 * 表现为「所有图标都不显示」（2026-10-24 真机排查结论，详见 Icon.tsx 注释）。
 *
 * 启动流程：`init()` 只恢复持久化的后端地址，**不**顺带拉第一页
 * （审查报告 P0-3：那样会和素材库挂载时的请求重复，冷启动两次第一页）。
 */

import { useEffect } from 'react'
import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { useInspirationStore } from '../hooks/useInspirations'
import { ThemeProvider, useTheme } from '../hooks/useTheme'
import ErrorBoundary from '../components/ErrorBoundary'
import ShareIntentBridge from '../components/ShareIntentBridge'

/** 导航栈：Stack 页面保留原生 header（系统返回手势与返回键不能丢） */
function RootNavigator() {
  const theme = useTheme()

  const stackOptions = {
    headerShown: false,
    contentStyle: { backgroundColor: theme.color.bgPage },
  } as const

  /** Stack 页的通用 header 样式（跟主题走，暗色下不出现白条） */
  const headerOptions = {
    headerShown: true,
    headerBackTitle: '返回',
    presentation: 'card',
    headerStyle: { backgroundColor: theme.color.bgSurface },
    headerTintColor: theme.color.textPrimary,
    headerTitleStyle: { color: theme.color.textPrimary },
  } as const

  return (
    <>
      <StatusBar style={theme.mode === 'dark' ? 'light' : 'dark'} />
      <Stack screenOptions={stackOptions}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="detail/[id]" options={{ ...headerOptions, title: '素材详情' }} />
        <Stack.Screen name="tasks" options={{ ...headerOptions, title: '后台任务' }} />
        <Stack.Screen name="collections" options={{ ...headerOptions, title: '收藏合集' }} />
        <Stack.Screen name="collection/[id]" options={{ ...headerOptions, title: '合集内容' }} />
        <Stack.Screen name="persons" options={{ ...headerOptions, title: '人物' }} />
        <Stack.Screen name="person/[id]" options={{ ...headerOptions, title: '人物作品' }} />
        <Stack.Screen name="tags" options={{ ...headerOptions, title: '标签管理' }} />
        <Stack.Screen name="generate" options={{ ...headerOptions, title: '出图记录' }} />
        <Stack.Screen name="generate/[id]" options={{ ...headerOptions, title: '候选图' }} />
      </Stack>
    </>
  )
}

export default function RootLayout() {
  useEffect(() => {
    // 启动初始化：只恢复持久化的自定义后端地址（列表由素材库挂载时拉取）
    void useInspirationStore.getState().init()
  }, [])

  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <ThemeProvider>
          {/* 分享 Intent：小红书/抖音/相册分享进来直接入库 */}
          <ShareIntentBridge />
          <RootNavigator />
        </ThemeProvider>
      </SafeAreaProvider>
    </ErrorBoundary>
  )
}
