/** 标签筛选弹层（搜索页用）：搜索 + 虚拟列表 + 多选。
 *
 * 为什么单独一个组件而不复用 TagEditor：TagEditor 的语义是「改某个素材的标签」
 * （点已关联即移除、保存即提交），这里只是「攒一组查询条件」，交互不同。
 * 相同点是性能约束：标签库有 2 万+，必须虚拟化 + 默认限量 + 搜索（见 CLAUDE.md）。
 */

import { useEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native'
import Icon from './Icon'
import type { TagCategoryGroup } from '../services/api'

const CATEGORY_LABELS: Record<string, string> = {
  style: '风格',
  item_type: '单品',
  color: '颜色',
  body_part: '穿着方式',
  fit: '版型',
  season: '季节',
  attribute: '属性',
  atmosphere: '氛围',
  design_detail: '设计细节',
  expression: '表情',
  free: '自定义',
}

/** 不带搜索词时展示的条数（避免开屏就渲染过多） */
const DEFAULT_LIMIT = 60
/** 带搜索词时的上限 */
const SEARCH_LIMIT = 200

interface Props {
  visible: boolean
  loading: boolean
  groups: TagCategoryGroup[]
  /** 已选标签名 */
  selected: string[]
  onClose: () => void
  /** 确认选择 */
  onConfirm: (names: string[]) => void
}

export default function TagFilterSheet({
  visible,
  loading,
  groups,
  selected,
  onClose,
  onConfirm,
}: Props) {
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string[]>(selected)

  useEffect(() => {
    if (visible) {
      setQuery('')
      setPicked(selected)
    }
  }, [visible, selected])

  const all = useMemo(() => {
    const flat: { id: number; name: string; category: string }[] = []
    for (const g of groups) {
      for (const t of g.tags) flat.push({ id: t.id, name: t.name, category: g.category })
    }
    return flat
  }, [groups])

  const q = query.trim().toLowerCase()
  const visibleTags = useMemo(() => {
    if (!q) return all.slice(0, DEFAULT_LIMIT)
    return all.filter((t) => t.name.toLowerCase().includes(q)).slice(0, SEARCH_LIMIT)
  }, [all, q])

  const toggle = (name: string) => {
    setPicked((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>按标签筛选</Text>
            <TouchableOpacity onPress={onClose} hitSlop={12} accessibilityLabel="关闭标签筛选">
              <Icon name="close" size={22} color="#6b7280" />
            </TouchableOpacity>
          </View>

          <View style={styles.searchRow}>
            <Icon name="search-outline" size={16} color="#9ca3af" />
            <TextInput
              style={styles.searchInput}
              value={query}
              onChangeText={setQuery}
              placeholder="搜索标签"
              placeholderTextColor="#9ca3af"
            />
            {query.length > 0 && (
              <TouchableOpacity onPress={() => setQuery('')} hitSlop={8}>
                <Icon name="close-circle" size={16} color="#d1d5db" />
              </TouchableOpacity>
            )}
          </View>

          {loading ? (
            <View style={styles.fillBox}>
              <ActivityIndicator color="#6366f1" />
              <Text style={styles.hintText}>正在加载标签…</Text>
            </View>
          ) : (
            <>
              <Text style={styles.hintLine}>
                {q ? `匹配 ${visibleTags.length} 个` : `共 ${all.length} 个标签，输入关键词缩小范围`}
              </Text>
              <FlatList
                data={visibleTags}
                keyExtractor={(t) => String(t.id)}
                style={styles.list}
                keyboardShouldPersistTaps="handled"
                initialNumToRender={14}
                windowSize={6}
                removeClippedSubviews
                ListEmptyComponent={<Text style={styles.hintText}>没有匹配的标签</Text>}
                renderItem={({ item }) => {
                  const on = picked.includes(item.name)
                  return (
                    <TouchableOpacity style={styles.row} onPress={() => toggle(item.name)}>
                      <Icon
                        name={on ? 'checkmark-circle' : 'ellipse-outline'}
                        size={16}
                        color={on ? '#6366f1' : '#d1d5db'}
                      />
                      <Text style={[styles.rowName, on && styles.rowNameActive]}>{item.name}</Text>
                      <Text style={styles.rowCategory}>
                        {CATEGORY_LABELS[item.category] || item.category}
                      </Text>
                    </TouchableOpacity>
                  )
                }}
              />
            </>
          )}

          <View style={styles.footer}>
            <Text style={styles.hint}>已选 {picked.length} 个</Text>
            <TouchableOpacity style={styles.cancelBtn} onPress={() => setPicked([])}>
              <Text style={styles.cancelText}>清空</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.okBtn} onPress={() => onConfirm(picked)}>
              <Text style={styles.okText}>确定</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  )
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.35)', justifyContent: 'flex-end' },
  sheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    height: '78%',
    paddingBottom: 10,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 8,
  },
  title: { fontSize: 16, fontWeight: '600', color: '#111827' },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 10,
    height: 38,
    borderRadius: 8,
    backgroundColor: '#f3f4f6',
  },
  searchInput: { flex: 1, fontSize: 14, color: '#111827', padding: 0 },
  fillBox: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  hintLine: { fontSize: 11, color: '#9ca3af', paddingHorizontal: 16, paddingBottom: 4 },
  list: { flex: 1, paddingHorizontal: 16 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#f9fafb',
  },
  rowName: { flex: 1, fontSize: 14, color: '#374151' },
  rowNameActive: { color: '#6366f1', fontWeight: '500' },
  rowCategory: { fontSize: 11, color: '#9ca3af' },
  hintText: { fontSize: 13, color: '#9ca3af', textAlign: 'center', paddingVertical: 16 },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  hint: { flex: 1, fontSize: 12, color: '#9ca3af' },
  cancelBtn: { paddingHorizontal: 14, paddingVertical: 9 },
  cancelText: { fontSize: 14, color: '#6b7280' },
  okBtn: {
    paddingHorizontal: 20,
    paddingVertical: 9,
    borderRadius: 8,
    backgroundColor: '#6366f1',
  },
  okText: { fontSize: 14, color: '#fff', fontWeight: '600' },
})
