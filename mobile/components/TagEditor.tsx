/** 标签编辑弹层：搜索标签库、勾选新增、移除已关联。
 *
 * 性能前提（2026-10-24 真机踩坑）：标签库有 **2 万+** 个标签，如果把全量标签
 * 一次性渲染成 chip 墙，JS 线程会直接卡死到 ANR。所以这里：
 * 1) 用 FlatList 虚拟列表（只渲染可视区的十几行）；
 * 2) 默认只显示前 60 个并提示「输入关键词搜索」，搜索时最多显示 200 条；
 * 3) 已关联标签单独一行常驻，数量少、可点即移除。
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
import type { InspirationTag, TagCategoryGroup } from '../services/api'

/** 标签分类中文名（与后端 category 枚举一致） */
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

/** 不带搜索词时最多展示多少个标签 */
const DEFAULT_LIMIT = 60
/** 带搜索词时最多展示多少个结果 */
const SEARCH_LIMIT = 200

interface Props {
  visible: boolean
  /** 标签库加载中 */
  loading: boolean
  /** 全部标签（按类别分组） */
  groups: TagCategoryGroup[]
  /** 当前素材已关联的标签 */
  current: InspirationTag[]
  onClose: () => void
  /** 提交新增（按名称，后端按名称查找或创建） */
  onAdd: (names: string[]) => void
  /** 解除某个已关联标签 */
  onRemove: (tagId: number) => void
  /** 提交中（禁按钮） */
  saving?: boolean
  /** 标签库加载失败（弱网下常见，给重试入口而不是空白） */
  loadError?: boolean
  onRetry?: () => void
}

interface FlatTag {
  id: number
  name: string
  category: string
}

export default function TagEditor({
  visible,
  loading,
  groups,
  current,
  onClose,
  onAdd,
  onRemove,
  saving = false,
  loadError = false,
  onRetry,
}: Props) {
  const [query, setQuery] = useState('')
  /** 本次勾选待新增的标签名 */
  const [pending, setPending] = useState<string[]>([])

  // 每次打开重置暂存态，避免上次的勾选残留
  useEffect(() => {
    if (visible) {
      setQuery('')
      setPending([])
    }
  }, [visible])

  const currentIds = useMemo(() => new Set(current.map((t) => t.tag.id)), [current])

  const allTags = useMemo<FlatTag[]>(() => {
    const flat: FlatTag[] = []
    for (const g of groups) {
      for (const t of g.tags) flat.push({ id: t.id, name: t.name, category: g.category })
    }
    return flat
  }, [groups])

  const total = allTags.length
  const q = query.trim().toLowerCase()

  const visibleTags = useMemo<FlatTag[]>(() => {
    if (!q) return allTags.slice(0, DEFAULT_LIMIT)
    return allTags.filter((t) => t.name.toLowerCase().includes(q)).slice(0, SEARCH_LIMIT)
  }, [allTags, q])

  // 搜索词精确命中与否决定是否给「新建」入口；模糊匹配但未精确命中时也给，避免建重复
  const exactExists = useMemo(
    () => q.length > 0 && allTags.some((t) => t.name.toLowerCase() === q),
    [allTags, q],
  )
  const canCreate = q.length > 0 && !exactExists

  const toggle = (name: string) => {
    setPending((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  const handleSave = () => {
    if (pending.length === 0 && canCreate) {
      onAdd([query.trim()])
      return
    }
    onAdd(pending)
  }

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          {/* 头部 */}
          <View style={styles.header}>
            <Text style={styles.title}>编辑标签</Text>
            <TouchableOpacity onPress={onClose} hitSlop={12} accessibilityLabel="关闭标签编辑">
              <Icon name="close" size={22} color="#6b7280" />
            </TouchableOpacity>
          </View>

          {/* 搜索 */}
          <View style={styles.searchRow}>
            <Icon name="search-outline" size={16} color="#9ca3af" />
            <TextInput
              style={styles.searchInput}
              value={query}
              onChangeText={setQuery}
              placeholder="搜索或输入新标签"
              placeholderTextColor="#9ca3af"
            />
            {query.length > 0 && (
              <TouchableOpacity onPress={() => setQuery('')} hitSlop={8}>
                <Icon name="close-circle" size={16} color="#d1d5db" />
              </TouchableOpacity>
            )}
          </View>

          {/* 已关联：常驻一行，点即移除 */}
          {current.length > 0 && (
            <View style={styles.linkedBox}>
              <Text style={styles.sectionLabel}>已关联（点击移除）</Text>
              <View style={styles.linkedChips}>
                {current.map((t) => (
                  <TouchableOpacity
                    key={t.tag.id}
                    style={styles.linkedChip}
                    onPress={() => onRemove(t.tag.id)}
                    disabled={saving}
                  >
                    <Icon name="checkmark" size={12} color="#fff" />
                    <Text style={styles.linkedChipText}>{String(t.tag.name ?? '')}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          )}

          {/* 新建入口 */}
          {canCreate && (
            <TouchableOpacity style={styles.createRow} onPress={handleSave} disabled={saving}>
              <Icon name="add-circle-outline" size={16} color="#6366f1" />
              <Text style={styles.createText}>新建标签「{query.trim()}」</Text>
            </TouchableOpacity>
          )}

          {/* 标签列表（虚拟化） */}
          {loading ? (
            <View style={styles.fillBox}>
              <ActivityIndicator color="#6366f1" />
              <Text style={styles.loadingText}>正在加载标签…</Text>
            </View>
          ) : loadError && groups.length === 0 ? (
            <View style={styles.fillBox}>
              <Text style={styles.loadingText}>标签库加载失败</Text>
              <TouchableOpacity style={styles.retryBtn} onPress={onRetry}>
                <Icon name="refresh" size={14} color="#fff" />
                <Text style={styles.retryText}>重试</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <>
              <Text style={styles.hintLine}>
                {q
                  ? `匹配 ${visibleTags.length} 个${visibleTags.length >= SEARCH_LIMIT ? '（仅显示前 200）' : ''}`
                  : `共 ${total} 个标签，输入关键词缩小范围`}
              </Text>
              <FlatList
                data={visibleTags}
                keyExtractor={(t) => String(t.id)}
                style={styles.list}
                keyboardShouldPersistTaps="handled"
                initialNumToRender={14}
                windowSize={6}
                removeClippedSubviews
                ListEmptyComponent={<Text style={styles.emptyText}>没有匹配的标签</Text>}
                renderItem={({ item }) => {
                  const linked = currentIds.has(item.id)
                  const chosen = pending.includes(item.name)
                  return (
                    <TouchableOpacity
                      style={styles.row}
                      onPress={() => (linked ? onRemove(item.id) : toggle(item.name))}
                      disabled={saving || linked}
                    >
                      <Icon
                        name={linked ? 'checkmark-circle' : chosen ? 'checkmark' : 'add-outline'}
                        size={16}
                        color={linked ? '#6366f1' : chosen ? '#6366f1' : '#c7d2fe'}
                      />
                      <Text style={[styles.rowName, (linked || chosen) && styles.rowNameActive]}>
                        {String(item.name ?? '')}
                      </Text>
                      <Text style={styles.rowCategory}>
                        {CATEGORY_LABELS[item.category] || item.category}
                      </Text>
                    </TouchableOpacity>
                  )
                }}
              />
            </>
          )}

          {/* 底部操作 */}
          <View style={styles.footer}>
            <Text style={styles.hint}>
              {pending.length > 0 ? `待添加 ${pending.length} 个` : '点已关联的标签可移除'}
            </Text>
            <TouchableOpacity style={styles.cancelBtn} onPress={onClose} disabled={saving}>
              <Text style={styles.cancelText}>取消</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.saveBtn, (saving || pending.length === 0) && styles.saveBtnDisabled]}
              onPress={handleSave}
              disabled={saving || pending.length === 0}
            >
              <Text style={styles.saveText}>{saving ? '保存中…' : '保存'}</Text>
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
    height: '82%',
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
  linkedBox: { paddingHorizontal: 16, paddingBottom: 6 },
  sectionLabel: { fontSize: 11, color: '#9ca3af', marginBottom: 6 },
  linkedChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  linkedChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: '#6366f1',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  linkedChipText: { fontSize: 12, color: '#fff' },
  createRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: '#f9fafb',
  },
  createText: { fontSize: 14, color: '#6366f1' },
  hintLine: { fontSize: 11, color: '#9ca3af', paddingHorizontal: 16, paddingBottom: 4 },
  list: { flex: 1, paddingHorizontal: 16 },
  emptyText: { fontSize: 13, color: '#9ca3af', paddingVertical: 20, textAlign: 'center' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 9,
    borderBottomWidth: 1,
    borderBottomColor: '#f9fafb',
  },
  rowName: { flex: 1, fontSize: 14, color: '#374151' },
  rowNameActive: { color: '#6366f1', fontWeight: '500' },
  rowCategory: { fontSize: 11, color: '#9ca3af' },
  fillBox: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  retryBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: '#6366f1',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 8,
  },
  retryText: { color: '#fff', fontSize: 13 },
  loadingText: { fontSize: 13, color: '#9ca3af' },
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
  saveBtn: {
    paddingHorizontal: 20,
    paddingVertical: 9,
    borderRadius: 8,
    backgroundColor: '#6366f1',
  },
  saveBtnDisabled: { backgroundColor: '#c7d2fe' },
  saveText: { fontSize: 14, color: '#fff', fontWeight: '600' },
})
