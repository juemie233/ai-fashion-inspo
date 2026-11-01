/** 自绘页头：Tab 内四个页面统一用它（原生 header 关掉）。
 *
 * 审查报告 P1-2：改前三套页头并存（素材库自绘 20px、搜索/采集/设置用原生 17pt 系统字、
 * Stack 页又是另一套），切 Tab 时标题基线与左右边距都在跳。现在：
 * - Tab 四个页面：`headerShown: false` + 本组件（22/28/600，左右 16，自动避让状态栏）
 * - Stack 页面：保留原生 header（系统返回手势与返回键不能丢）
 */

import type { ReactNode } from 'react'
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import AppText from './AppText'
import { useStyles } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  title: string
  /** 左侧自定义（一般不用，返回交给原生 header） */
  left?: ReactNode
  /** 右侧操作区（图标按钮，已自带 44 触控区） */
  right?: ReactNode
  style?: StyleProp<ViewStyle>
}

export default function ScreenHeader({ title, left, right, style }: Props) {
  const insets = useSafeAreaInsets()
  const styles = useStyles(makeStyles)
  return (
    <View style={[styles.container, { paddingTop: insets.top + 8 }, style]}>
      {left}
      <AppText variant="display" numberOfLines={1} style={styles.title}>
        {title}
      </AppText>
      {right ? <View style={styles.actions}>{right}</View> : null}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
      paddingBottom: t.space.sm,
      backgroundColor: t.color.bgSurface,
    },
    title: { flex: 1 },
    actions: { flexDirection: 'row', alignItems: 'center' },
  })
