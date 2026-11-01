/** 底部弹层：统一的拖动条 / 安全区 / 遮罩关闭 / 键盘避让。
 *
 * 审查报告 P1-9：改前三个弹层各自 `Modal + View`，`paddingBottom: 10` 写死、
 * 高度写死 82%/78%/62%、遮罩是 `View` 点空白关不掉、输入框聚焦后页脚被键盘盖住。
 * 现在高度改成「内容自适应 + maxHeight 88%」，页脚吃 `insets.bottom`，遮罩可点关闭。
 */

import type { ReactNode } from 'react'
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import AppText from './AppText'
import IconButton from './IconButton'
import { useStyles } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  visible: boolean
  title?: string
  onClose: () => void
  children: ReactNode
  /** 固定在底部的操作区（保存/确定），会被键盘避让 */
  footer?: ReactNode
  testID?: string
  style?: StyleProp<ViewStyle>
}

export default function BottomSheet({
  visible,
  title,
  onClose,
  children,
  footer,
  testID,
  style,
}: Props) {
  const insets = useSafeAreaInsets()
  const styles = useStyles(makeStyles)

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <KeyboardAvoidingView
        style={styles.fill}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* 遮罩是 Pressable：点空白处关闭（改前是 View，关不掉） */}
        <Pressable
          style={styles.backdrop}
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel="关闭弹层"
        />
        <View testID={testID} style={[styles.sheet, style]}>
          <View style={styles.handle} />
          {title ? (
            <View style={styles.header}>
              <AppText variant="title" numberOfLines={1} style={styles.title}>
                {title}
              </AppText>
              <IconButton
                name="close"
                onPress={onClose}
                accessibilityLabel={`关闭${title}`}
                color={undefined}
              />
            </View>
          ) : null}
          <View style={styles.content}>{children}</View>
          {footer ? (
            <View style={[styles.footer, { paddingBottom: insets.bottom + 12 }]}>{footer}</View>
          ) : (
            <View style={{ height: insets.bottom + 12 }} />
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    fill: { flex: 1, justifyContent: 'flex-end' },
    backdrop: { flex: 1, backgroundColor: t.color.scrim },
    sheet: {
      maxHeight: '88%',
      backgroundColor: t.color.bgSurface,
      borderTopLeftRadius: t.radius.sheet,
      borderTopRightRadius: t.radius.sheet,
      paddingTop: t.space.sm,
    },
    handle: {
      alignSelf: 'center',
      width: 36,
      height: 4,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.border,
      marginBottom: t.space.sm,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: t.space.sm,
      paddingLeft: t.space.lg,
      paddingRight: t.space.xs,
    },
    title: { flex: 1 },
    content: { flexShrink: 1 },
    footer: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
      paddingTop: t.space.md,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.divider,
    },
  })
