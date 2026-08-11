/** 根布局：配置 Expo Router 导航栈。 */

import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'

export default function RootLayout() {
  return (
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
      </Stack>
    </SafeAreaProvider>
  )
}
