/** 全屏图片查看器：点详情页大图后铺满屏幕浏览。
 *
 * 为什么不用第三方库：只需要「铺满 + 点击关闭」两个能力，自己写 60 行就够，
 * 不引入 react-native-image-zoom-viewer 这类长期不维护的依赖。
 *
 * 交互约定（与 Web 端灯箱保持一致的心智）：
 * - 铺满屏幕、黑底、按原比例完整显示（不裁切）
 * - 点任意位置关闭；右上角另有显式关闭按钮（怕用户不知道能点）
 */

import { Image, Modal, Pressable, StyleSheet, Text, View } from 'react-native'
import Icon from './Icon'

interface Props {
  /** 是否显示 */
  visible: boolean
  /** 图片完整 URL */
  uri: string
  /** 关闭回调 */
  onClose: () => void
}

export default function ImageViewer({ visible, uri, onClose }: Props) {
  return (
    <Modal
      visible={visible}
      transparent={false}
      animationType="fade"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="关闭全屏图片">
        <Image source={{ uri }} style={styles.image} resizeMode="contain" />

        <Pressable style={styles.closeBtn} onPress={onClose} hitSlop={12}>
          <Icon name="close" size={26} color="#fff" />
        </Pressable>

        <Text style={styles.hint}>点击任意处关闭</Text>
        <View style={styles.bottomSpacer} />
      </Pressable>
    </Modal>
  )
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: '#000',
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: {
    width: '100%',
    height: '100%',
  },
  closeBtn: {
    position: 'absolute',
    top: 44,
    right: 16,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(0,0,0,0.45)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  hint: {
    position: 'absolute',
    bottom: 48,
    color: 'rgba(255,255,255,0.7)',
    fontSize: 12,
  },
  bottomSpacer: { height: 0 },
})
