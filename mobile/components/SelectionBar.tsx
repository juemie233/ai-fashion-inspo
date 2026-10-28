/** 多选态的底部操作栏：收藏 / 加合集 / 移入垃圾桶 / 退出多选。 */

import { StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import Icon from './Icon'

interface Props {
  /** 已选数量 */
  count: number
  /** 其中已收藏的数量，用于决定「收藏」按钮是收藏还是取消收藏 */
  favoriteCount: number
  busy?: boolean
  onToggleFavorite: () => void
  onAddToCollection: () => void
  onTrash: () => void
  onCancel: () => void
}

export default function SelectionBar({
  count,
  favoriteCount,
  busy = false,
  onToggleFavorite,
  onAddToCollection,
  onTrash,
  onCancel,
}: Props) {
  // 全部已收藏时按钮变成「取消收藏」，否则「收藏」
  const allFavorite = count > 0 && favoriteCount === count

  return (
    <View style={styles.bar}>
      <View style={styles.info}>
        <Text style={styles.countText}>已选 {count} 张</Text>
        <TouchableOpacity onPress={onCancel} hitSlop={8}>
          <Text style={styles.cancelText}>退出多选</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.actions}>
        <TouchableOpacity
          testID="batch-favorite"
          style={styles.action}
          onPress={onToggleFavorite}
          disabled={busy}
        >
          <Icon name={allFavorite ? 'heart-dislike-outline' : 'heart-outline'} size={20} color="#6366f1" />
          <Text style={styles.actionText}>{allFavorite ? '取消收藏' : '收藏'}</Text>
        </TouchableOpacity>

        <TouchableOpacity
          testID="batch-collection"
          style={styles.action}
          onPress={onAddToCollection}
          disabled={busy}
        >
          <Icon name="albums-outline" size={20} color="#6366f1" />
          <Text style={styles.actionText}>加合集</Text>
        </TouchableOpacity>

        <TouchableOpacity
          testID="batch-trash"
          style={styles.action}
          onPress={onTrash}
          disabled={busy}
        >
          <Icon name="trash-outline" size={20} color="#ef4444" />
          <Text style={[styles.actionText, { color: '#ef4444' }]}>移入垃圾桶</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#e5e7eb',
    paddingBottom: 6,
  },
  info: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  countText: { fontSize: 13, color: '#374151', fontWeight: '500' },
  cancelText: { fontSize: 13, color: '#6b7280' },
  actions: { flexDirection: 'row', paddingHorizontal: 8, paddingBottom: 6 },
  action: { flex: 1, alignItems: 'center', gap: 3, paddingVertical: 4 },
  actionText: { fontSize: 11, color: '#6366f1' },
})
