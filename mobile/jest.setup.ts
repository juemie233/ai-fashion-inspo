/** Jest 全局设置：mock 掉原生模块。 */

// AsyncStorage 的原生模块在 Node 里不存在，用官方 jest mock 代替
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
)

// expo-router 依赖原生导航栈；单测里只用到 useRouter()
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  useRoute: () => ({ params: {} }),
  useLocalSearchParams: () => ({}),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
  Tabs: ({ children }: { children?: unknown }) => children ?? null,
}))

// @expo/vector-icons 会去读原生已加载字体表（Jest 环境没有 loadedNativeFonts），
// 直接用 Text 顶替：图标本身渲染出来就是文本，不影响「裸字符串」检查。
jest.mock('@expo/vector-icons', () => {
  const React = require('react')
  const { Text } = require('react-native')
  const makeIcon = (name: string) =>
    function Icon({ ...props }: Record<string, unknown>) {
      return React.createElement(Text, { ...props, testID: `icon-${name}` }, '')
    }
  return {
    Ionicons: makeIcon('Ionicons'),
    MaterialIcons: makeIcon('MaterialIcons'),
    MaterialCommunityIcons: makeIcon('MaterialCommunityIcons'),
    FontAwesome: makeIcon('FontAwesome'),
    Feather: makeIcon('Feather'),
  }
})
