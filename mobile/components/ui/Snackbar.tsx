/** 底部轻提示：一条文案 + 可选操作（撤销 / 查看）。
 *
 * 用途（审查报告 P1-8、§4.3）：标签保存反馈、批量操作结果。
 * 定位为绝对定位浮层，调用方把它放在页面根节点最后（`pointerEvents="box-none"` 的外层）。
 */

import { useEffect } from 'react'
import { Pressable, StyleSheet, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import AppText from './AppText'
import { useStyles } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  visible: boolean
  message: string
  /** 右侧操作文案（如「撤销」） */
  actionLabel?: string
  onAction?: () => void
  /** 自动消失回调（到时间或点关闭） */
  onHide: () => void
  /** 自动消失时长（毫秒），默认 3000 */
  duration?: number
}

export default function Snackbar({
  visible,
  message,
  actionLabel,
  onAction,
  onHide,
  duration = 3000,
}: Props) {
  const insets = useSafeAreaInsets()
  const styles = useStyles(makeStyles)

  useEffect(() => {
    if (!visible) return
    const timer = setTimeout(onHide, duration)
    return () => clearTimeout(timer)
  }, [visible, duration, onHide])

  if (!visible) return null

  return (
    <View pointerEvents="box-none" style={[styles.wrap, { paddingBottom: insets.bottom + 16 }]}>
      <View style={styles.bar}>
        <AppText variant="bodySm" color="textPrimary" style={styles.message} numberOfLines={2}>
          {message}
        </AppText>
        {actionLabel && onAction ? (
          <Pressable
            onPress={onAction}
            hitSlop={10}
            accessibilityRole="button"
            style={styles.action}
          >
            <AppText variant="bodySm" color="brandText">
              {actionLabel}
            </AppText>
          </Pressable>
        ) : null}
      </View>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    wrap: {
      position: 'absolute',
      left: 0,
      right: 0,
      bottom: 0,
      paddingHorizontal: t.space.lg,
      alignItems: 'center',
    },
    bar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      maxWidth: 560,
      width: '100%',
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.md,
      borderRadius: t.radius.control,
      backgroundColor: t.color.bgSurface,
      borderWidth: 1,
      borderColor: t.color.border,
      // 仅 Toast/浮层允许阴影（§3.3）
      shadowColor: t.color.shadow,
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.12,
      shadowRadius: 8,
      elevation: 4,
    },
    message: { flex: 1 },
    action: { paddingHorizontal: t.space.xs },
  })
