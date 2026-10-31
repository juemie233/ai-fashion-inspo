/** 合集选择器：把选中的素材加进已有合集，或现场新建一个。
 *
 * 只在多选态用（见 docs/移动端移植方案.md：手机上不做合集拖拽排序）。
 */

import { useEffect, useState } from 'react'
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
import type { CollectionItem } from '../services/api'

interface Props {
  visible: boolean
  /** 要加入的素材数量（仅用于文案） */
  count: number
  collections: CollectionItem[]
  loading: boolean
  /** 提交中 */
  saving?: boolean
  onClose: () => void
  /** 选中已有合集 */
  onPick: (collection: CollectionItem) => void
  /** 新建合集 */
  onCreate: (name: string) => void
}

export default function CollectionPicker({
  visible,
  count,
  collections,
  loading,
  saving = false,
  onClose,
  onPick,
  onCreate,
}: Props) {
  const [name, setName] = useState('')

  useEffect(() => {
    if (visible) setName('')
  }, [visible])

  /** 只列一级手动合集（二级收藏夹和智能合集不作为投放目标，避免误操作） */
  const targets = collections.filter((c) => !c.parent_id && c.kind === 'manual')

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>加入合集</Text>
            <TouchableOpacity onPress={onClose} hitSlop={12} accessibilityLabel="关闭合集选择">
              <Icon name="close" size={22} color="#6b7280" />
            </TouchableOpacity>
          </View>

          <Text style={styles.hint}>把选中的 {count} 张素材加入下面某个合集</Text>

          {/* 新建合集 */}
          <View style={styles.createRow}>
            <TextInput
              style={styles.input}
              value={name}
              onChangeText={setName}
              placeholder="新建合集名称"
              placeholderTextColor="#9ca3af"
            />
            <TouchableOpacity
              style={[styles.createBtn, (!name.trim() || saving) && styles.createBtnDisabled]}
              onPress={() => onCreate(name.trim())}
              disabled={!name.trim() || saving}
            >
              <Text style={styles.createBtnText}>新建并加入</Text>
            </TouchableOpacity>
          </View>

          {loading ? (
            <View style={styles.loading}>
              <ActivityIndicator color="#6366f1" />
            </View>
          ) : (
            <FlatList
              data={targets}
              keyExtractor={(c) => String(c.id)}
              style={styles.list}
              ListEmptyComponent={<Text style={styles.empty}>还没有合集，可以在上面新建一个</Text>}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={styles.row}
                  onPress={() => onPick(item)}
                  disabled={saving}
                >
                  <Icon name="albums-outline" size={18} color="#6366f1" />
                  <Text style={styles.rowName} numberOfLines={1}>
                    {item.name}
                  </Text>
                  <Text style={styles.rowCount}>{item.item_count ?? 0} 张</Text>
                </TouchableOpacity>
              )}
            />
          )}
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
    height: '62%',
    paddingBottom: 10,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 6,
  },
  title: { fontSize: 16, fontWeight: '600', color: '#111827' },
  hint: { fontSize: 12, color: '#9ca3af', paddingHorizontal: 16, paddingBottom: 8 },
  createRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  input: {
    flex: 1,
    height: 38,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 8,
    paddingHorizontal: 10,
    fontSize: 14,
    color: '#111827',
  },
  createBtn: {
    paddingHorizontal: 12,
    justifyContent: 'center',
    borderRadius: 8,
    backgroundColor: '#6366f1',
  },
  createBtnDisabled: { backgroundColor: '#c7d2fe' },
  createBtnText: { color: '#fff', fontSize: 13 },
  loading: { paddingVertical: 40, alignItems: 'center' },
  list: { flex: 1, paddingHorizontal: 16 },
  empty: { fontSize: 13, color: '#9ca3af', paddingVertical: 24, textAlign: 'center' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#f9fafb',
  },
  rowName: { flex: 1, fontSize: 14, color: '#374151' },
  rowCount: { fontSize: 11, color: '#9ca3af' },
})
