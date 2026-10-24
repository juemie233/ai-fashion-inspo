/** 相似素材横向列表：详情页底部展示向量检索出来的同类素材。 */

import { FlatList, Image, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import type { Inspiration } from '../services/api'

interface Props {
  items: Inspiration[]
  /** 缩略图地址解析（不同页面 baseUrl 来源不同，交给调用方拼） */
  resolveUri: (item: Inspiration) => string
  onPress: (id: string) => void
}

export default function SimilarList({ items, resolveUri, onPress }: Props) {
  if (items.length === 0) return null

  return (
    <View style={styles.section}>
      <Text style={styles.title}>相似素材</Text>
      <FlatList
        data={items}
        horizontal
        showsHorizontalScrollIndicator={false}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.card}
            activeOpacity={0.8}
            onPress={() => onPress(item.id)}
          >
            <Image source={{ uri: resolveUri(item) }} style={styles.image} />
          </TouchableOpacity>
        )}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  section: { paddingTop: 16, borderTopWidth: 1, borderTopColor: '#f3f4f6' },
  title: {
    fontSize: 14,
    fontWeight: '600',
    color: '#374151',
    paddingHorizontal: 16,
    marginBottom: 8,
  },
  list: { paddingHorizontal: 16, gap: 8 },
  card: { width: 96, height: 128, borderRadius: 8, overflow: 'hidden', backgroundColor: '#f3f4f6' },
  image: { width: '100%', height: '100%', resizeMode: 'cover' },
})
