/** 全屏图片查看器：点详情页大图后铺满屏幕浏览。
 *
 * 为什么不用第三方库：只需要「铺满 + 点击关闭」两个能力，自己写 60 行就够，
 * 不引入 react-native-image-zoom-viewer 这类长期不维护的依赖。
 *
 * 交互约定（与 Web 端灯箱保持一致的心智）：
 * - 铺满屏幕、黑底、按原比例完整显示（不裁切）
 * - 点任意位置关闭；右上角另有显式关闭按钮（怕用户不知道能点）
 *
 * 审查修复：关闭按钮吃 `insets.top`（刘海/灵动岛遮挡），图片改用 expo-image
 * （内存/磁盘缓存，来回看同一张不重新下载）。
 */

import { Modal, Pressable, StyleSheet, View } from 'react-native'
import { Image } from 'expo-image'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Icon from './Icon'
import AppText from './ui/AppText'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'

interface Props {
  /** 是否显示 */
  visible: boolean
  /** 图片完整 URL */
  uri: string
  /** 关闭回调 */
  onClose: () => void
}

export default function ImageViewer({ visible, uri, onClose }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const insets = useSafeAreaInsets()

  return (
    <Modal
      visible={visible}
      transparent={false}
      animationType="fade"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="关闭全屏图片">
        <Image
          source={{ uri }}
          style={styles.image}
          contentFit="contain"
          transition={150}
          cachePolicy="memory-disk"
        />

        <Pressable
          style={[styles.closeBtn, { top: insets.top + 12 }]}
          onPress={onClose}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="关闭"
        >
          <Icon name="close" size={theme.icon.lg} color={theme.color.textOnMedia} />
        </Pressable>

        <View style={[styles.hint, { bottom: insets.bottom + 24 }]}>
          <AppText variant="caption" color="textOnMedia">
            点击任意处关闭
          </AppText>
        </View>
      </Pressable>
    </Modal>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    backdrop: {
      flex: 1,
      backgroundColor: t.color.videoBackdrop,
      alignItems: 'center',
      justifyContent: 'center',
    },
    image: { width: '100%', height: '100%' },
    closeBtn: {
      position: 'absolute',
      right: t.space.lg,
      width: t.touch,
      height: t.touch,
      borderRadius: t.touch / 2,
      backgroundColor: t.color.scrim,
      alignItems: 'center',
      justifyContent: 'center',
    },
    hint: {
      position: 'absolute',
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.xs,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.scrim,
    },
  })
