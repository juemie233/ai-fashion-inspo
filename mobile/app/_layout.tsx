/** 根布局：配置 Expo Router 导航栈。 */

import { useEffect } from 'react'
import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { useInspirationStore } from '../hooks/useInspirations'

export default function RootLayout() {
  useEffect(() => {
    // 启动初始化：恢复持久化的自定义后端地址并加载素材列表
    void useInspirationStore.getState().init()
  }, [])

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen
          name="settings"
          options={{
            headerShown: true,
            title: '设置',
            headerBackTitle: '返回',
            presentation: 'card',
          }}
        />
        <Stack.Screen
          name="detail/[id]"
          options={{
            headerShown: true,
            title: '素材详情',
            headerBackTitle: '返回',
            presentation: 'card',
          }}
        />
      </Stack>
    </SafeAreaProvider>
  )
}
