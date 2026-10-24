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

// 相册/相机：默认返回空结果，具体用例在测试文件里用 jest.spyOn 覆盖
jest.mock('expo-image-picker', () => ({
  requestMediaLibraryPermissionsAsync: jest.fn(async () => ({ granted: true })),
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
  launchCameraAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
}))

// @expo/vector-icons 会去读原生已加载字体表（Jest 环境没有 loadedNativeFonts），
// 直接用 Text 顶替：图标本身渲染出来就是文本，不影响「裸字符串」检查。
jest.mock('@expo/vector-icons', () => {
  const React = require('react')
  const { Text } = require('react-native')

  // 任意图标名都返回一个私有区码点，避免自研 Icon 渲染成 '?' 干扰断言
  const glyphMap = new Proxy({} as Record<string, number>, { get: () => 0xe900 })

  /** 造一个「渲染成 Text」的图标替身；glyphMap 供自研 Icon 组件查码点 */
  const makeIcon = (name: string) =>
    (props: Record<string, unknown>) =>
      React.createElement(Text, { ...props, testID: `icon-${name}` }, '')

  const ionicons = Object.assign(makeIcon('Ionicons'), { glyphMap })

  return {
    Ionicons: ionicons,
    MaterialIcons: makeIcon('MaterialIcons'),
    MaterialCommunityIcons: makeIcon('MaterialCommunityIcons'),
    FontAwesome: makeIcon('FontAwesome'),
    Feather: makeIcon('Feather'),
  }
})
