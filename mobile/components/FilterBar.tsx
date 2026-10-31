/** 素材库顶部筛选栏：筛选胶囊 + 排序切换。
 *
 * 触屏取舍（见 docs/移动端.md）：Web 的多维筛选抽屉在手机上收起为
 * 一排可横滑的胶囊；排序只保留手机上有意义的几种，其余留在 Web。
 */

import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import Icon from './Icon'
import type { ListFilter, SortKey } from '../services/api'

/** 筛选胶囊定义：key 用于比较当前态 */
const FILTERS: { key: string; label: string; filter: ListFilter }[] = [
  { key: 'all', label: '全部', filter: {} },
  { key: 'fav', label: '收藏', filter: { is_favorite: true } },
  { key: 'tagged', label: '已标注', filter: { tag_status: 'tagged' } },
  { key: 'untagged', label: '未标注', filter: { tag_status: 'untagged' } },
]

/** 排序选项（手机侧够用的四种） */
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: '最新' },
  { key: 'oldest', label: '最早' },
  { key: 'rating', label: '评分' },
  { key: 'random', label: '随机' },
]

interface Props {
  filter: ListFilter
  sort: SortKey
  onFilter: (patch: ListFilter) => void
  onSort: (sort: SortKey) => void
}

/** 当前筛选命中的是哪个胶囊 */
function activeKey(filter: ListFilter): string {
  if (filter.is_favorite) return 'fav'
  if (filter.tag_status === 'tagged') return 'tagged'
  if (filter.tag_status === 'untagged') return 'untagged'
  return 'all'
}

export default function FilterBar({ filter, sort, onFilter, onSort }: Props) {
  const active = activeKey(filter)
  const sortLabel = SORTS.find((s) => s.key === sort)?.label ?? '最新'

  return (
    <View style={styles.container}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {FILTERS.map((f) => {
          const isActive = f.key === active
          return (
            <TouchableOpacity
              key={f.key}
              testID={`filter-${f.key}`}
              style={[styles.chip, isActive && styles.chipActive]}
              onPress={() => onFilter(f.filter)}
            >
              <Text style={[styles.chipText, isActive && styles.chipTextActive]}>{f.label}</Text>
            </TouchableOpacity>
          )
        })}
      </ScrollView>

      <TouchableOpacity
        style={styles.sortBtn}
        onPress={() => {
          const idx = SORTS.findIndex((s) => s.key === sort)
          onSort(SORTS[(idx + 1) % SORTS.length].key)
        }}
      >
        <Icon name="swap-vertical-outline" size={14} color="#6b7280" />
        <Text style={styles.sortText}>{sortLabel}</Text>
      </TouchableOpacity>
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    gap: 8,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  chips: { gap: 8, paddingRight: 8, alignItems: 'center' },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 14,
    backgroundColor: '#f3f4f6',
  },
  chipActive: { backgroundColor: '#6366f1' },
  chipText: { fontSize: 13, color: '#374151' },
  chipTextActive: { color: '#fff', fontWeight: '500' },
  sortBtn: { flexDirection: 'row', alignItems: 'center', gap: 3, paddingLeft: 8 },
  sortText: { fontSize: 12, color: '#6b7280' },
})
