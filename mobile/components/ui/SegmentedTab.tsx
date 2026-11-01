/** 分段控件：页面级切换（人物「穿搭博主 / 模特」、搜索「关键词 / 语义」）。
 *
 * 与 `Chip` 的区别是语义：这里是「换一个页面/模式」，不是「加一个筛选条件」。
 * 视觉上用「轨道 + 选中滑块」，和筛选胶囊完全区分开（审查报告 P1-4）。
 */

import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import AppText from './AppText'
import { useStyles } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

export interface SegmentedItem {
  key: string
  label: string
}

interface Props {
  items: SegmentedItem[]
  value: string
  onChange: (key: string) => void
  /** testID 前缀：`${prefix}-${key}`，便于测试精确点击 */
  testIDPrefix?: string
  style?: StyleProp<ViewStyle>
}

export default function SegmentedTab({ items, value, onChange, testIDPrefix, style }: Props) {
  const styles = useStyles(makeStyles)
  return (
    <View style={[styles.track, style]}>
      {items.map((item) => {
        const active = item.key === value
        return (
          <Pressable
            key={item.key}
            testID={testIDPrefix ? `${testIDPrefix}-${item.key}` : undefined}
            onPress={() => onChange(item.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            style={[styles.segment, active && styles.segmentActive]}
          >
            <AppText variant="bodySm" color={active ? 'brandText' : 'textSecondary'}>
              {item.label}
            </AppText>
          </Pressable>
        )
      })}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    track: {
      flexDirection: 'row',
      backgroundColor: t.color.bgSubtle,
      borderRadius: t.radius.control,
      padding: 3,
      gap: 2,
    },
    segment: {
      flex: 1,
      minHeight: 34,
      alignItems: 'center',
      justifyContent: 'center',
      borderRadius: t.radius.control - 3,
    },
    segmentActive: { backgroundColor: t.color.bgSurface },
  })
