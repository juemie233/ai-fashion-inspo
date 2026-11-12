/** 全屏图片查看器：双指缩放 / 双击放大 / 点按开关控件 + 保存到相册 + 系统分享。
 *
 * 为什么不用第三方库：只需要「缩放 + 保存/分享 + 关闭」四个能力；`react-native-gesture-handler`
 * 和 `react-native-reanimated` 本来就随 Expo 模板装好了，自己写 200 行可控且不引新体系。
 *
 * 交互约定（P0-3）：
 * - **单击**：显示/隐藏控件（不再「点一下就关」，放大后误触不会退出）；
 * - **双击**：放大到 2.5×，再双击还原；
 * - **双指**：1×~4× 缩放；松开时若已缩回 1× 则位置归零；
 * - **拖动**：仅在放大后可拖动，松手夹在图片边界内；
 * - **Android 返回键**：放大状态先还原，再按一次才关闭；
 * - 保存/分享：远程图先下载到缓存再操作，用完即删（见 `utils/mediaActions.ts`）。
 */

import { useCallback, useEffect, useState } from 'react'
import { Alert, Modal, Pressable, StyleSheet, View, useWindowDimensions } from 'react-native'
import { Gesture, GestureDetector, GestureHandlerRootView } from 'react-native-gesture-handler'
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated'
import { Image } from 'expo-image'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Icon from './Icon'
import AppText from './ui/AppText'
import Button from './ui/Button'
import { useStyles, useTheme } from '../hooks/useTheme'
import { saveImageToLibrary, shareImage } from '../utils/mediaActions'
import { successFeedback, warningFeedback } from '../utils/haptics'
import type { Theme } from '../constants/theme'

/** 缩放范围 */
const MIN_SCALE = 1
const MAX_SCALE = 4
/** 双击放大的目标倍数 */
const DOUBLE_TAP_SCALE = 2.5
/** 双击判定窗口（毫秒） */
const DOUBLE_TAP_MS = 260

interface Props {
  /** 是否显示 */
  visible: boolean
  /** 图片完整 URL（远程 http 或本地 file） */
  uri: string
  /** 关闭回调 */
  onClose: () => void
}

export default function ImageViewer({ visible, uri, onClose }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const insets = useSafeAreaInsets()
  const { width, height } = useWindowDimensions()

  /** 控件（关闭按钮、底部说明与操作）是否可见 */
  const [controls, setControls] = useState(true)
  /** 正在执行的保存/分享（禁重复点击） */
  const [busy, setBusy] = useState<'save' | 'share' | null>(null)

  const scale = useSharedValue(1)
  const startScale = useSharedValue(1)
  const offsetX = useSharedValue(0)
  const offsetY = useSharedValue(0)
  const startX = useSharedValue(0)
  const startY = useSharedValue(0)

  const resetZoom = useCallback(() => {
    scale.value = withTiming(1, { duration: 160 })
    offsetX.value = withTiming(0, { duration: 160 })
    offsetY.value = withTiming(0, { duration: 160 })
    startScale.value = 1
    startX.value = 0
    startY.value = 0
  }, [offsetX, offsetY, scale, startScale, startX, startY])

  // 每次打开重置缩放与控件状态（上一张放大的倍数不该带到下一张）
  useEffect(() => {
    if (!visible) return
    setControls(true)
    setBusy(null)
    scale.value = 1
    offsetX.value = 0
    offsetY.value = 0
    startScale.value = 1
    startX.value = 0
    startY.value = 0
  }, [visible, offsetX, offsetY, scale, startScale, startX, startY])

  const toggleControls = useCallback(() => {
    setControls((prev) => !prev)
  }, [])

  /** 双指缩放 */
  const pinch = Gesture.Pinch()
    .onBegin(() => {
      startScale.value = scale.value
    })
    .onUpdate((event) => {
      scale.value = Math.min(MAX_SCALE, Math.max(MIN_SCALE, startScale.value * event.scale))
    })
    .onEnd(() => {
      if (scale.value <= MIN_SCALE) {
        scale.value = withTiming(1, { duration: 120 })
        offsetX.value = withTiming(0, { duration: 120 })
        offsetY.value = withTiming(0, { duration: 120 })
        startX.value = 0
        startY.value = 0
      }
    })

  /** 拖动：只在放大后生效 */
  const pan = Gesture.Pan()
    .onBegin(() => {
      startX.value = offsetX.value
      startY.value = offsetY.value
    })
    .onUpdate((event) => {
      if (scale.value <= MIN_SCALE) return
      offsetX.value = startX.value + event.translationX
      offsetY.value = startY.value + event.translationY
    })
    .onEnd(() => {
      if (scale.value <= MIN_SCALE) return
      // 夹在图片边界内：放大后图片可移动的最大距离（以屏幕为基准的近似）
      const maxX = (width * (scale.value - 1)) / 2
      const maxY = (height * (scale.value - 1)) / 2
      offsetX.value = withTiming(Math.max(-maxX, Math.min(maxX, offsetX.value)), { duration: 120 })
      offsetY.value = withTiming(Math.max(-maxY, Math.min(maxY, offsetY.value)), { duration: 120 })
    })

  /** 双击放大 / 还原 */
  const doubleTap = Gesture.Tap()
    .numberOfTaps(2)
    .maxDuration(DOUBLE_TAP_MS)
    .onEnd(() => {
      if (scale.value > MIN_SCALE) {
        scale.value = withTiming(1, { duration: 160 })
        offsetX.value = withTiming(0, { duration: 160 })
        offsetY.value = withTiming(0, { duration: 160 })
        startX.value = 0
        startY.value = 0
        startScale.value = 1
      } else {
        scale.value = withTiming(DOUBLE_TAP_SCALE, { duration: 160 })
        startScale.value = DOUBLE_TAP_SCALE
      }
    })

  /** 单击：显示/隐藏控件（与双击互斥：先判双击） */
  const singleTap = Gesture.Tap()
    .numberOfTaps(1)
    .onEnd(() => {
      runOnJS(toggleControls)()
    })

  const gesture = Gesture.Simultaneous(
    Gesture.Exclusive(doubleTap, singleTap),
    Gesture.Simultaneous(pinch, pan),
  )

  const animatedStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: offsetX.value },
      { translateY: offsetY.value },
      { scale: scale.value },
    ],
  }))

  /** Android 返回键：放大状态先还原，再按一次才关闭 */
  const handleRequestClose = () => {
    if (scale.value > MIN_SCALE) {
      resetZoom()
      return
    }
    onClose()
  }

  const runAction = async (kind: 'save' | 'share') => {
    if (busy) return
    setBusy(kind)
    try {
      const result = kind === 'save' ? await saveImageToLibrary(uri) : await shareImage(uri)
      if (result.ok) {
        successFeedback()
        Alert.alert(kind === 'save' ? '已保存' : '分享', result.message)
      } else {
        warningFeedback()
        Alert.alert(kind === 'save' ? '保存失败' : '分享失败', result.message)
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <Modal
      visible={visible}
      transparent={false}
      animationType="fade"
      onRequestClose={handleRequestClose}
      statusBarTranslucent
    >
      {/* Modal 在 Android 上是独立的原生视图树，手势必须再包一层 RootView */}
      <GestureHandlerRootView style={styles.root}>
        <View style={styles.backdrop}>
          <GestureDetector gesture={gesture}>
            <Animated.View style={[styles.imageWrap, animatedStyle]}>
              <Image
                testID="image-viewer-image"
                source={{ uri }}
                style={styles.image}
                contentFit="contain"
                transition={150}
                cachePolicy="memory-disk"
              />
            </Animated.View>
          </GestureDetector>

          {controls ? (
            <>
              <Pressable
                style={[styles.closeBtn, { top: insets.top + 12 }]}
                onPress={onClose}
                hitSlop={12}
                accessibilityRole="button"
                accessibilityLabel="关闭"
              >
                <Icon name="close" size={theme.icon.lg} color={theme.color.textOnMedia} />
              </Pressable>

              <View style={[styles.footer, { paddingBottom: insets.bottom + 16 }]}>
                <AppText variant="caption" color="textOnMedia" style={styles.hint}>
                  双指缩放 · 双击放大 · 点按隐藏按钮
                </AppText>
                <View style={styles.actions}>
                  <Button
                    title="保存到相册"
                    icon="download-outline"
                    variant="secondary"
                    loading={busy === 'save'}
                    disabled={busy !== null}
                    onPress={() => void runAction('save')}
                    testID="viewer-save"
                  />
                  <Button
                    title="分享"
                    icon="share-outline"
                    variant="secondary"
                    loading={busy === 'share'}
                    disabled={busy !== null}
                    onPress={() => void runAction('share')}
                    testID="viewer-share"
                  />
                </View>
              </View>
            </>
          ) : null}
        </View>
      </GestureHandlerRootView>
    </Modal>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    root: { flex: 1 },
    backdrop: {
      flex: 1,
      backgroundColor: t.color.videoBackdrop,
      alignItems: 'center',
      justifyContent: 'center',
    },
    imageWrap: { width: '100%', height: '100%' },
    image: { width: '100%', height: '100%' },
    closeBtn: {
      position: 'absolute',
      right: t.space.lg,
      width: t.touch,
      height: t.touch,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.scrim,
      alignItems: 'center',
      justifyContent: 'center',
    },
    footer: {
      position: 'absolute',
      left: 0,
      right: 0,
      bottom: 0,
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
    },
    hint: { paddingHorizontal: t.space.md, paddingVertical: t.space.xs },
    actions: { flexDirection: 'row', gap: t.space.md },
  })
