/** 底部 Tab 导航布局：素材库 / 搜索 / 采集 / 设置。
 *
 * 审查修复（P1-2）：四个 Tab 页面统一 `headerShown: false`，页头由各页自己用
 * `ui/ScreenHeader` 画（22/28/600），不再出现「素材库自绘 20px + 其他页原生 17pt」两套标题。
 * Tab 栏高度 = 52 + 安全区，标签 12px、图标 24。
 */

import { useWindowDimensions } from 'react-native'
import { Tabs } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Icon from '../../components/Icon'
import { useTheme } from '../../hooks/useTheme'

/** Tab 栏基础高度（不含安全区）；系统字体放大时按 fontScale 追加，避免标签被裁 */
const TAB_BAR_BASE_HEIGHT = 56

export default function TabLayout() {
  const theme = useTheme()
  const insets = useSafeAreaInsets()
  const { fontScale } = useWindowDimensions()
  // 真机踩坑（0.8.0）：固定 52 高 + 系统字体 1.15× 时，标签下缘会被屏幕边缘裁掉；
  // 这里按字体缩放追加高度，并把 paddingTop 收到 4
  const barHeight = TAB_BAR_BASE_HEIGHT + insets.bottom + Math.round(Math.max(0, fontScale - 1) * 24)

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.color.brandText,
        tabBarInactiveTintColor: theme.color.textTertiary,
        tabBarStyle: {
          backgroundColor: theme.color.bgSurface,
          borderTopColor: theme.color.divider,
          height: barHeight,
          paddingTop: 4,
          paddingBottom: insets.bottom,
        },
        tabBarLabelStyle: {
          fontSize: theme.font.caption.fontSize,
          fontWeight: '500',
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: '素材库',
          tabBarIcon: ({ color }) => (
            <Icon name="images-outline" size={theme.icon.lg} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="search"
        options={{
          title: '搜索',
          tabBarIcon: ({ color }) => (
            <Icon name="search-outline" size={theme.icon.lg} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="capture"
        options={{
          title: '采集',
          tabBarIcon: ({ color }) => (
            <Icon name="camera-outline" size={theme.icon.lg} color={color} />
          ),
        }}
      />
      {/* 设置做成 Tab 而不是只放在首页 header：真机上齿轮太小且只在「素材库」页可见，
          用户找不到改后端地址的入口（2026-10-23 实测反馈）。
          审查后首页齿轮已删（P1-11），设置入口只保留这里。 */}
      <Tabs.Screen
        name="settings"
        options={{
          title: '设置',
          tabBarIcon: ({ color }) => (
            <Icon name="settings-outline" size={theme.icon.lg} color={color} />
          ),
        }}
      />
    </Tabs>
  )
}
