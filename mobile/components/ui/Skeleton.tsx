/** 骨架屏：首屏加载占位（网格 / 列表两种）。
 *
 * 审查报告 P1-6：改前 9 个页面用整屏 `ActivityIndicator`，素材库干脆没有加载态，
 * 标签页还写「正在加载标签库…（2 万+ 个，首次约需 8 秒）」向用户解释为什么慢。
 * 现在一律用骨架占位，不解释耗时。
 */

import { useEffect, useRef } from 'react'
import { Animated, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

/** 呼吸动画：0.45 ↔ 0.9 透明度 */
function usePulse() {
  const value = useRef(new Animated.Value(0.45)).current
  useEffect(() => {
    // Jest 环境（JEST_WORKER_ID 由 jest 注入）不启动循环动画：它会持续触发
    // Animated 的 setState，满屏 act(...) 警告并拖慢用例；静态 0.45 透明度足够断言。
    if (process.env.JEST_WORKER_ID) return
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(value, { toValue: 0.9, duration: 700, useNativeDriver: true }),
        Animated.timing(value, { toValue: 0.45, duration: 700, useNativeDriver: true }),
      ]),
    )
    loop.start()
    return () => loop.stop()
  }, [value])
  return value
}

interface BlockProps {
  style?: StyleProp<ViewStyle>
}

/** 单块骨架（灰色圆角矩形） */
export function SkeletonBlock({ style }: BlockProps) {
  const theme = useTheme()
  const opacity = usePulse()
  return (
    <Animated.View
      style={[
        { backgroundColor: theme.color.bgSubtle, borderRadius: theme.radius.control, opacity },
        style,
      ]}
    />
  )
}

interface GridProps {
  /** 块数，默认 6 */
  count?: number
  /** 列数，默认 2 */
  columns?: number
  /** 宽高比（宽/高），默认 3:4 */
  ratio?: number
  testID?: string
}

/** 网格骨架（素材库 / 合集详情 / 标签的图墙） */
export function SkeletonGrid({ count = 6, columns = 2, ratio = 0.75, testID }: GridProps) {
  const styles = useStyles(makeStyles)
  const rows = Math.ceil(count / columns)
  return (
    <View testID={testID ?? 'skeleton-grid'} style={styles.grid}>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <View key={rowIndex} style={styles.gridRow}>
          {Array.from({ length: columns }).map((__, colIndex) =>
            rowIndex * columns + colIndex < count ? (
              <SkeletonBlock key={colIndex} style={[styles.gridCell, { aspectRatio: ratio }]} />
            ) : (
              <View key={colIndex} style={styles.gridCell} />
            ),
          )}
        </View>
      ))}
    </View>
  )
}

interface ListProps {
  rows?: number
  testID?: string
}

/** 列表骨架（任务 / 出图 / 人物 / 标签行） */
export function SkeletonList({ rows = 5, testID }: ListProps) {
  const styles = useStyles(makeStyles)
  return (
    <View testID={testID ?? 'skeleton-list'} style={styles.list}>
      {Array.from({ length: rows }).map((_, index) => (
        <View key={index} style={styles.listRow}>
          <SkeletonBlock style={styles.avatar} />
          <View style={styles.listTexts}>
            <SkeletonBlock style={styles.lineWide} />
            <SkeletonBlock style={styles.lineNarrow} />
          </View>
        </View>
      ))}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    grid: { padding: t.space.md, gap: t.space.md },
    gridRow: { flexDirection: 'row', gap: t.space.md },
    gridCell: { flex: 1, borderRadius: t.radius.card },
    list: { padding: t.space.lg, gap: t.space.lg },
    listRow: { flexDirection: 'row', alignItems: 'center', gap: t.space.md },
    avatar: { width: 44, height: 44, borderRadius: t.radius.pill },
    listTexts: { flex: 1, gap: t.space.sm },
    lineWide: { height: 14, width: '70%' },
    lineNarrow: { height: 12, width: '40%' },
  })
