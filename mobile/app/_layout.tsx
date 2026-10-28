/** 根布局：配置 Expo Router 导航栈。
 *
 * 图标说明：图标统一走 `components/Icon.tsx`（直接用内嵌字体渲染字形），
 * 不要再用 `<Ionicons>` —— 它会因 `Font.isLoaded('ionicons')` 判假而渲染空文本，
 * 表现为「所有图标都不显示」（2026-10-24 真机排查结论，详见 Icon.tsx 注释）。
 */

import { useEffect } from 'react'
import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { useInspirationStore } from '../hooks/useInspirations'
import ErrorBoundary from '../components/ErrorBoundary'

export default function RootLayout() {
  useEffect(() => {
    // 启动初始化：恢复持久化的自定义后端地址并加载素材列表
    void useInspirationStore.getState().init()
  }, [])

  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen
            name="detail/[id]"
            options={{
              headerShown: true,
              title: '素材详情',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="tasks"
            options={{
              headerShown: true,
              title: '后台任务',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="collections"
            options={{
              headerShown: true,
              title: '收藏合集',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="collection/[id]"
            options={{
              headerShown: true,
              title: '合集内容',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="persons"
            options={{
              headerShown: true,
              title: '人物',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="person/[id]"
            options={{
              headerShown: true,
              title: '人物作品',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
          <Stack.Screen
            name="tags"
            options={{
              headerShown: true,
              title: '标签管理',
              headerBackTitle: '返回',
              presentation: 'card',
            }}
          />
        </Stack>
      </SafeAreaProvider>
    </ErrorBoundary>
  )
}
