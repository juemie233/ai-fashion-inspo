/** 素材库顶部筛选栏：筛选胶囊（横滑）+ 排序入口。
 *
 * 触屏取舍（见 docs/移动端.md）：Web 的多维筛选抽屉在手机上收起为一排可横滑的胶囊；
 * 排序只保留手机上有意义的四种，其余留在 Web。
 *
 * 审查修复：
 * - P1-4 胶囊改用 `ui/Chip`（浅底 + 选中主色描边），与页面级切换的分段控件区分开
 * - P1-7 排序不再「点一次切下一个」，改为打开底部弹层单选，按钮显示当前排序
 * - 2026-11-12 真机验收：1.15× 字体下「排序：最新」把第 4 个胶囊（未标注）挤出屏幕，
 *   按钮文案改成只显示当前值（无障碍标签保留「排序：…」），腾出的宽度给筛选胶囊
 */

import { Pressable, ScrollView, StyleSheet, View } from 'react-native'
import Chip from './ui/Chip'
import Icon from './Icon'
import AppText from './ui/AppText'
import { activeFilterKey, FILTER_OPTIONS, sortLabel } from '../constants/labels'
import { useStyles, useTheme } from '../hooks/useTheme'
import type { Theme } from '../constants/theme'
import type { ListFilter, SortKey } from '../services/api'

interface Props {
  filter: ListFilter
  sort: SortKey
  onFilter: (filter: ListFilter) => void
  /** 打开排序弹层 */
  onOpenSort: () => void
}

export default function FilterBar({ filter, sort, onFilter, onOpenSort }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const active = activeFilterKey(filter)

  return (
    <View style={styles.container}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chips}
        style={styles.chipsScroll}
      >
        {FILTER_OPTIONS.map((option) => (
          <Chip
            key={option.key}
            testID={`filter-${option.key}`}
            label={option.label}
            selected={option.key === active}
            onPress={() => onFilter(option.filter)}
          />
        ))}
      </ScrollView>

      <Pressable
        testID="sort-button"
        onPress={onOpenSort}
        accessibilityRole="button"
        accessibilityLabel={`排序：${sortLabel(sort)}`}
        style={({ pressed }) => [styles.sortBtn, pressed && styles.pressed]}
      >
        {/* 只显示当前值：1.15× 字体下「排序：最新」会挤掉第 4 个筛选胶囊（真机发现） */}
        <AppText variant="caption" color="textSecondary" numberOfLines={1}>
          {sortLabel(sort)}
        </AppText>
        <Icon name="chevron-down" size={theme.icon.sm} color={theme.color.textSecondary} />
      </Pressable>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    chipsScroll: { flex: 1 },
    chips: { gap: t.space.sm, alignItems: 'center', paddingRight: t.space.xs },
    // 排序入口给足 44 高的触控区（P0-5）
    sortBtn: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 2,
      minHeight: t.touch,
      paddingLeft: t.space.sm,
    },
    pressed: { opacity: 0.7 },
  })
