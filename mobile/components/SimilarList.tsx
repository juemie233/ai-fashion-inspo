/** 相似素材横向列表：详情页底部展示向量检索出来的同类素材。 */

import { FlatList, StyleSheet, View } from 'react-native'
import MediaCard from './ui/MediaCard'
import AppText from './ui/AppText'
import { useStyles } from '../hooks/useTheme'
import type { Inspiration } from '../services/api'
import type { Theme } from '../constants/theme'

/** 横滑卡片宽度 */
const CARD_WIDTH = 96

interface Props {
  items: Inspiration[]
  /** 缩略图地址解析（不同页面 baseUrl 来源不同，交给调用方拼） */
  resolveUri: (item: Inspiration) => string
  onPress: (id: string) => void
}

export default function SimilarList({ items, resolveUri, onPress }: Props) {
  const styles = useStyles(makeStyles)
  if (items.length === 0) return null

  return (
    <View style={styles.section}>
      <AppText variant="subtitle" style={styles.title}>
        相似素材
      </AppText>
      <FlatList
        data={items}
        horizontal
        showsHorizontalScrollIndicator={false}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <View style={styles.cardWrap}>
            <MediaCard uri={resolveUri(item)} onPress={() => onPress(item.id)} />
          </View>
        )}
      />
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    section: {
      paddingTop: t.space.lg,
      gap: t.space.sm,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.divider,
    },
    title: { paddingHorizontal: t.space.lg },
    list: { paddingHorizontal: t.space.lg, gap: t.space.sm },
    cardWrap: { width: CARD_WIDTH },
  })
