/** 图片卡：素材库 / 搜索 / 合集详情 / 人物作品 / 生成候选共用一套规格。
 *
 * 审查报告 P1-3：改前同一个「素材卡片」有三种比例（0.75 / 2/3 / 1）、两种圆角（10 / 12）、
 * 三种信息层，同一个 App 里看起来像三个产品。现在比例由 `ratio` 决定，其余
 * （圆角 14、渐变遮罩 56、角标位置、44 触控的收藏按钮）全部由组件内部决定。
 *
 * 图上文字可读性（P0-6）：文字压在图片上必须先垫 `transparent → mediaGradientEnd` 的
 * 56px 渐变（改前是 9px 白字直接压原图 + rgba(0,0,0,.45) 小方块，浅色照片上几乎看不见）。
 */

import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import { Image } from 'expo-image'
import { LinearGradient } from 'expo-linear-gradient'
import AppText from './AppText'
import Badge from './Badge'
import Icon from '../Icon'
import IconButton from './IconButton'
import { useStyles, useTheme } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

interface Props {
  /** 缩略图完整 URL */
  uri: string
  /** 宽/高，默认 0.75（3:4）；合集封面 1（正方形） */
  ratio?: number
  /** 底部标签（最多 2 个，其余折叠成 +N） */
  tags?: string[]
  /** 最多显示几个标签，默认 2 */
  maxTags?: number
  /** 左上角来源角标 */
  badgeText?: string
  /** 是否视频（左下角「视频」角标） */
  video?: boolean
  /** 是否已收藏 */
  favorite?: boolean
  /** 是否显示收藏按钮（多选态隐藏） */
  showFavorite?: boolean
  onToggleFavorite?: () => void
  /** 多选选中态 */
  selected?: boolean
  onPress?: () => void
  onLongPress?: () => void
  /** 加载占位底色（一般传素材的 dominant_colors 首色） */
  placeholderColor?: string
  testID?: string
  style?: StyleProp<ViewStyle>
  /** 右侧自定义角标（生成候选图用「已入库」） */
  cornerBadge?: string
}

export default function MediaCard({
  uri,
  ratio = 0.75,
  tags,
  maxTags = 2,
  badgeText,
  video = false,
  favorite = false,
  showFavorite = false,
  onToggleFavorite,
  selected = false,
  onPress,
  onLongPress,
  placeholderColor,
  testID,
  style,
  cornerBadge,
}: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)

  const shownTags = (tags ?? []).filter((t) => Boolean(t)).slice(0, maxTags)
  const restCount = Math.max(0, (tags ?? []).length - shownTags.length)
  const hasBottom = shownTags.length > 0 || restCount > 0 || video

  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={280}
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.card,
        { aspectRatio: ratio },
        selected && styles.cardSelected,
        pressed && styles.pressed,
        style,
      ]}
    >
      {/* 占位底色先画（图片解码前不闪白块）；顺序不能反，后画的会盖住图片 */}
      <View
        pointerEvents="none"
        style={[
          styles.image,
          styles.placeholder,
          { backgroundColor: placeholderColor ?? theme.color.bgSubtle },
        ]}
      />
      <Image
        source={{ uri }}
        style={styles.image}
        contentFit="cover"
        transition={150}
        cachePolicy="memory-disk"
        recyclingKey={uri}
        placeholderContentFit="cover"
      />

      {/* 底部渐变 + 标签/视频角标（分行，不重叠） */}
      {hasBottom ? (
        <LinearGradient
          pointerEvents="none"
          colors={['transparent', theme.color.mediaGradientEnd] as const}
          style={styles.gradient}
        />
      ) : null}
      <View pointerEvents="none" style={styles.bottom}>
        {video ? (
          <View style={styles.videoBadge}>
            <Icon name="play" size={theme.icon.sm} color={theme.color.textOnMedia} />
            <AppText variant="micro" color="textOnMedia">
              视频
            </AppText>
          </View>
        ) : null}
        {shownTags.length > 0 || restCount > 0 ? (
          <View style={styles.tagRow}>
            {shownTags.map((tag, index) => (
              <AppText
                key={`${tag}-${index}`}
                variant="micro"
                color="textOnMedia"
                numberOfLines={1}
                style={styles.tagText}
              >
                {tag}
              </AppText>
            ))}
            {restCount > 0 ? (
              <AppText variant="micro" color="textOnMedia">
                +{restCount}
              </AppText>
            ) : null}
          </View>
        ) : null}
      </View>

      {/* 左上：来源角标 */}
      {badgeText ? (
        <Badge tone="onMedia" text={badgeText} style={styles.sourceBadge} />
      ) : null}

      {/* 右上：收藏（44 触控区，与图片边缘留 4px）或选中勾选 */}
      {selected ? (
        <View pointerEvents="none" style={styles.check}>
          <Icon name="checkmark" size={theme.icon.sm} color={theme.color.textOnMedia} />
        </View>
      ) : showFavorite ? (
        <IconButton
          name={favorite ? 'heart' : 'heart-outline'}
          size={theme.icon.md}
          color={favorite ? theme.color.dangerText : theme.color.textOnMedia}
          background="scrim"
          onPress={onToggleFavorite}
          accessibilityLabel={favorite ? '取消收藏' : '收藏'}
          style={styles.favorite}
        />
      ) : null}

      {cornerBadge ? (
        <Badge tone="brand" text={cornerBadge} style={styles.cornerBadge} />
      ) : null}

      {/* 选中态：15% 主色遮罩 + 2px 主色描边（比改前 35% 遮罩不遮内容） */}
      {selected ? (
        <View pointerEvents="none" style={styles.selectedOverlay} />
      ) : null}
    </Pressable>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    card: {
      flex: 1,
      borderRadius: t.radius.card,
      overflow: 'hidden',
      backgroundColor: t.color.bgSubtle,
    },
    cardSelected: { borderWidth: 2, borderColor: t.color.brandText },
    pressed: { opacity: 0.9 },
    image: { ...StyleSheet.absoluteFillObject },
    placeholder: { position: 'absolute' },
    gradient: { position: 'absolute', left: 0, right: 0, bottom: 0, height: 56 },
    bottom: {
      position: 'absolute',
      left: t.space.sm,
      right: t.space.sm,
      bottom: 6,
      gap: t.space.xs,
    },
    videoBadge: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 3,
      alignSelf: 'flex-start',
      paddingHorizontal: 5,
      paddingVertical: 1,
      borderRadius: t.radius.badge,
      backgroundColor: t.color.scrim,
    },
    tagRow: { flexDirection: 'row', alignItems: 'center', gap: t.space.xs, flexWrap: 'wrap' },
    tagText: { fontWeight: '500' },
    sourceBadge: { position: 'absolute', top: 6, left: 6 },
    favorite: { position: 'absolute', top: 0, right: 0 },
    check: {
      position: 'absolute',
      top: 10,
      right: 10,
      width: 24,
      height: 24,
      borderRadius: t.radius.pill,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: t.color.brandFill,
    },
    cornerBadge: { position: 'absolute', bottom: 6, right: 6 },
    selectedOverlay: {
      ...StyleSheet.absoluteFillObject,
      backgroundColor: t.color.brandOverlay,
    },
  })
