/** 底部 Tab 导航布局：画廊 / 搜索 / 采集。 */

import { TouchableOpacity } from 'react-native'
import { Tabs, useRouter } from 'expo-router'
import Icon from '../../components/Icon'

export default function TabLayout() {
  const router = useRouter()

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: '#6366f1',
        tabBarInactiveTintColor: '#9ca3af',
        tabBarStyle: {
          borderTopColor: '#f3f4f6',
          paddingBottom: 4,
          height: 56,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: '500',
        },
        headerStyle: {
          backgroundColor: '#fff',
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: '素材库',
          // 首页有自己的顶栏（素材库标题 + 合集 + 设置入口），这里关掉重复的 header
          headerShown: false,
          tabBarIcon: ({ color, size }) => (
            <Icon name="images-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="search"
        options={{
          title: '搜索',
          tabBarIcon: ({ color, size }) => (
            <Icon name="search-outline" size={size} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="capture"
        options={{
          title: '采集',
          tabBarIcon: ({ color, size }) => (
            <Icon name="camera-outline" size={size} color={color} />
          ),
        }}
      />
      {/* 设置做成 Tab 而不是只放在首页 header：真机上齿轮太小且只在「素材库」页可见，
          用户找不到改后端地址的入口（2026-10-23 实测反馈） */}
      <Tabs.Screen
        name="settings"
        options={{
          title: '设置',
          tabBarIcon: ({ color, size }) => (
            <Icon name="settings-outline" size={size} color={color} />
          ),
        }}
      />
    </Tabs>
  )
}
