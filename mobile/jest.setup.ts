/** Jest 全局设置：mock 掉原生模块。 */

// RNTL 默认 1s 的异步等待在满负载并行跑时容易偶发超时（Modal 动画 + 多套件竞争）。
// 2026-10-28 CI 实测：GitHub runner 只有 2 核，同一套件本地 29s / CI 65s，
// 5s 的等待窗口在 CI 上会偶发假失败，放宽到 15s（真正的回归仍会被超时挡住）。
const { configure } = require('@testing-library/react-native')
configure({ asyncUtilTimeout: 15000 })

// 单个用例的硬超时同样放宽：CI 上渲染密集用例耗时是本地 2~3 倍。
// 注意 jest 对**同步**用例无法中断（card.fuzz 的整屏渲染就是同步循环），
// 所以这一行只兜「异步用例卡死」，同步用例的耗时靠拆小用例控制。
jest.setTimeout(60000)

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

// 视频播放器：expo-video 是原生模块，Node 下直接 import 会崩（读 undefined.prototype）。
// 用 View 顶替 VideoView，播放器实例给最小可用替身；具体断言在 video.test.tsx 里覆盖。
jest.mock('expo-video', () => {
  const React = require('react')
  const { View } = require('react-native')
  const player = { play: () => {}, pause: () => {}, loop: false, playing: false }
  return {
    __esModule: true,
    useVideoPlayer: () => player,
    VideoView: (props: Record<string, unknown>) => React.createElement(View, props),
  }
})

// reanimated：手势/动画库自带 mock（共享值 read/write 变普通对象）。
// 不 mock 的话 Jest 里没有 UI 线程，useAnimatedStyle 会直接抛错。
jest.mock('react-native-reanimated', () => require('react-native-reanimated/mock'))

// 安全区：组件里的 useSafeAreaInsets 在没有 SafeAreaProvider 的测试树里会抛错，
// 测试环境统一给 0 边距（真机行为由 App 里的 SafeAreaProvider 提供）
jest.mock('react-native-safe-area-context', () => {
  const actual = jest.requireActual('react-native-safe-area-context')
  return {
    ...actual,
    useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  }
})

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

// expo-image 是原生视图（ExpoImage）：Node 下用 View 顶替，
// `contentFit`/`transition`/`cachePolicy` 这些属性对断言无意义，原样透传即可。
jest.mock('expo-image', () => {
  const React = require('react')
  const { View } = require('react-native')
  // 真实 API 是静态方法 `Image.loadAsync(uri)`（上传队列恢复时用它探测本地文件），
  // 所以挂到组件函数上而不是模块导出上
  const MockImage = Object.assign(
    (props: Record<string, unknown>) => React.createElement(View, props),
    {
      loadAsync: jest.fn(async () => ({ width: 1, height: 1 })),
      // 设置页「清理缓存」与列表图片预取（P1）
      clearMemoryCache: jest.fn(async () => true),
      clearDiskCache: jest.fn(async () => true),
      prefetch: jest.fn(async () => true),
    },
  )
  return { __esModule: true, Image: MockImage }
})

// 渐变遮罩同上是原生视图：渲染成 View 并保留 children（不影响断言与裸字符串检查）
jest.mock('expo-linear-gradient', () => {
  const React = require('react')
  const { View } = require('react-native')
  return {
    __esModule: true,
    LinearGradient: (props: Record<string, unknown>) =>
      React.createElement(View, props, props.children as React.ReactNode),
  }
})

// 触觉反馈：Node 环境没有振动马达，统一空实现（具体调用由 utils/haptics 的用例断言）
jest.mock('expo-haptics', () => ({
  __esModule: true,
  selectionAsync: jest.fn(async () => {}),
  impactAsync: jest.fn(async () => {}),
  notificationAsync: jest.fn(async () => {}),
  ImpactFeedbackStyle: { Light: 'light', Medium: 'medium', Heavy: 'heavy' },
  NotificationFeedbackType: { Success: 'success', Warning: 'warning', Error: 'error' },
}))


// P2：逐用例清理渲染树与定时器，避免 React 更新悬挂 / Jest 强制退出。
// 用 require 而不是顶层 import：本文件里大量 jest.mock 依赖 require 顺序。
const { cleanup } = require('@testing-library/react-native')

afterEach(() => {
  cleanup()
  jest.clearAllTimers()
  jest.useRealTimers()
})
