/** 标签轻量管理：浏览 + 搜索 + 改名。
 *
 * 只做三件事（见 docs/移动端移植方案.md）：合并/删除/颜色/排序留在 Web ——
 * 破坏性操作需要对比多个标签，手机上误触代价高。
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
import Icon from '../components/Icon'
import { fetchTagGroups, invalidateTagCache, renameTag, type TagCategoryGroup } from '../services/api'

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

/** 不带搜索词时最多展示多少（标签库 2 万+，必须限量 + 虚拟化） */
const DEFAULT_LIMIT = 80
const SEARCH_LIMIT = 200

interface FlatTag {
  id: number
  name: string
  category: string
}

export default function TagsScreen() {
  const [groups, setGroups] = useState<TagCategoryGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  /** 正在改名的标签 */
  const [editing, setEditing] = useState<FlatTag | null>(null)
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  /** 改名失败原因（必须显示在弹层里：列表区的错误会被遮罩挡住） */
  const [renameError, setRenameError] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      // 走会话级缓存：同一个 App 生命周期内只付一次 8 秒
      setGroups(await fetchTagGroups())
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const all = useMemo<FlatTag[]>(() => {
    const flat: FlatTag[] = []
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

  const openEdit = (tag: FlatTag) => {
    setEditing(tag)
    setDraftName(tag.name)
    setRenameError('')
  }

  /** 关闭改名弹层（错误提示一并清掉） */
  const closeEdit = () => {
    setEditing(null)
    setRenameError('')
  }

  const submitRename = async () => {
    if (!editing) return
    const name = draftName.trim()
    if (!name || name === editing.name) {
      closeEdit()
      return
    }
    setSaving(true)
    setRenameError('')
    try {
      await renameTag(editing.id, name)
      // 改名后让其它页面（标签筛选/编辑器）下次拿到最新数据
      invalidateTagCache()
      // 本地同步：直接改名字，避免整库重拉（2 万+ 个标签太重）
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          tags: g.tags.map((t) => (t.id === editing.id ? { ...t, name } : t)),
        })),
      )
      closeEdit()
    } catch {
      setRenameError('改名失败：名称可能重复')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#6366f1" />
        <Text style={styles.loadingHint}>正在加载标签库…（2 万+ 个，首次约需 8 秒）</Text>
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <View style={styles.searchBar}>
        <Icon name="search" size={16} color="#9ca3af" />
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

      <Text style={styles.hint}>
        {q
          ? `匹配 ${visibleTags.length} 个${visibleTags.length >= SEARCH_LIMIT ? '（仅显示前 200）' : ''}`
          : `共 ${all.length} 个标签，输入关键词缩小范围`}
      </Text>

      {error !== '' && <Text style={styles.errorText}>{error}</Text>}

      <FlatList
        data={visibleTags}
        keyExtractor={(t) => String(t.id)}
        initialNumToRender={14}
        windowSize={6}
        removeClippedSubviews
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.emptyText}>没有匹配的标签</Text>}
        renderItem={({ item }) => (
          <TouchableOpacity
            testID={`tag-${item.id}`}
            style={styles.row}
            onPress={() => openEdit(item)}
          >
            <Text style={styles.rowName} numberOfLines={1}>
              {item.name}
            </Text>
            <Text style={styles.rowCategory}>
              {CATEGORY_LABELS[item.category] || item.category}
            </Text>
            <Icon name="create-outline" size={15} color="#d1d5db" />
          </TouchableOpacity>
        )}
      />

      {/* 改名弹层 */}
      <Modal visible={editing !== null} transparent animationType="fade" onRequestClose={closeEdit}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>重命名标签</Text>
            <Text style={styles.modalHint}>原名：{editing?.name ?? ''}</Text>
            <TextInput
              style={styles.modalInput}
              value={draftName}
              onChangeText={setDraftName}
              autoFocus
              placeholder="新名称"
              placeholderTextColor="#9ca3af"
            />
            {renameError !== '' && <Text style={styles.modalError}>{renameError}</Text>}
            <View style={styles.modalActions}>
              <TouchableOpacity style={styles.modalCancel} onPress={closeEdit}>
                <Text style={styles.modalCancelText}>取消</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalSave, saving && styles.modalSaveDisabled]}
                onPress={() => void submitRename()}
                disabled={saving}
              >
                <Text style={styles.modalSaveText}>{saving ? '保存中…' : '保存'}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#f9fafb', gap: 10 },
  loadingHint: { fontSize: 12, color: '#9ca3af' },
  searchBar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#fff',
    margin: 12,
    paddingHorizontal: 10,
    height: 38,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  searchInput: { flex: 1, fontSize: 14, color: '#111827', padding: 0 },
  hint: { fontSize: 11, color: '#9ca3af', paddingHorizontal: 14, paddingBottom: 6 },
  errorText: { fontSize: 12, color: '#ef4444', paddingHorizontal: 14, paddingBottom: 6 },
  list: { paddingHorizontal: 12, paddingBottom: 24 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: '#fff',
    paddingHorizontal: 12,
    paddingVertical: 11,
    marginBottom: 6,
    borderRadius: 8,
  },
  rowName: { flex: 1, fontSize: 14, color: '#374151' },
  rowCategory: { fontSize: 11, color: '#9ca3af' },
  emptyText: { textAlign: 'center', paddingVertical: 24, fontSize: 13, color: '#9ca3af' },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  modalCard: { width: '100%', backgroundColor: '#fff', borderRadius: 12, padding: 16 },
  modalTitle: { fontSize: 16, fontWeight: '600', color: '#111827', marginBottom: 6 },
  modalHint: { fontSize: 12, color: '#9ca3af', marginBottom: 10 },
  modalInput: {
    height: 40,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 8,
    paddingHorizontal: 10,
    fontSize: 15,
    color: '#111827',
  },
  modalError: { fontSize: 12, color: '#ef4444', marginTop: 8 },
  modalActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 14 },
  modalCancel: { paddingHorizontal: 14, paddingVertical: 9 },
  modalCancelText: { fontSize: 14, color: '#6b7280' },
  modalSave: {
    paddingHorizontal: 20,
    paddingVertical: 9,
    borderRadius: 8,
    backgroundColor: '#6366f1',
  },
  modalSaveDisabled: { backgroundColor: '#c7d2fe' },
  modalSaveText: { fontSize: 14, color: '#fff', fontWeight: '600' },
})
