/** 素材详情页：大图浏览 + 全屏看图 + 标签编辑 + 移入垃圾桶 + 相似推荐。
 *
 * 审查修复：
 * - P0-8 主图按**真实比例**渲染（`Image.getSize` 缓存），上限屏高 72%；
 *   letterbox 用素材主色（`dominant_colors` 首色）而不是死灰色块
 * - P1-5 错误态给「重试」，不再只有一行灰字
 * - P1-8 标签移除改为「弹层内标记 → 保存统一提交」（可确认、可反悔）
 * - P2-9 「删除」收进「更多」弹层，避免与「收藏」并排误触
 * - 深色模式：所有颜色走令牌
 */

import { useState, useEffect, useCallback } from 'react'
import {
  View,
  ScrollView,
  StyleSheet,
  Alert,
  Dimensions,
  Linking,
  Pressable,
  useWindowDimensions,
} from 'react-native'
import { Image } from 'expo-image'
import { useLocalSearchParams, useRouter } from 'expo-router'
import Icon from '../../components/Icon'
import AppText from '../../components/ui/AppText'
import IconButton from '../../components/ui/IconButton'
import Button from '../../components/ui/Button'
import BottomSheet from '../../components/ui/BottomSheet'
import ErrorState from '../../components/ui/ErrorState'
import Snackbar from '../../components/ui/Snackbar'
import { SkeletonBlock } from '../../components/ui/Skeleton'
import {
  apiClient,
  addTagsToInspiration,
  removeTagFromInspiration,
  trashInspiration,
  fetchSimilarInspirations,
  fetchTagGroups,
  getFileUrl,
  type Inspiration,
  type InspirationTag,
  type TagCategoryGroup,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { useAspectRatio } from '../../hooks/useAspectRatio'
import { detailImageHeight } from '../../utils/imageAspect'
import { sourceLabel } from '../../utils/sourceLabel'
import { dominantColor } from '../../utils/dominantColor'
import { tagCategoryLabel } from '../../constants/labels'
import { successFeedback, warningFeedback } from '../../utils/haptics'
import ImageViewer from '../../components/ImageViewer'
import VideoPlayer from '../../components/VideoPlayer'
import SimilarList from '../../components/SimilarList'
import TagEditor from '../../components/TagEditor'
import type { Theme } from '../../constants/theme'

const { height: SCREEN_HEIGHT } = Dimensions.get('window')
/** 主图最高占屏高比例（超过就留白，避免一张长图把操作栏挤出屏幕） */
const MAX_IMAGE_HEIGHT = SCREEN_HEIGHT * 0.72
/** 拿不到真实比例时的兜底（4:5，与改前一致） */
const FALLBACK_RATIO = 0.8

/** 判断「原始链接」是否为可访问的页面链接（排除图片/视频 CDN 直链） */
function isSourceLinkValid(url: string | null | undefined): boolean {
  if (!url) return false
  // 提取 hostname（RN 环境下避免依赖不完全的 URL 实现）
  const m = url.match(/^[a-z][a-z0-9+.-]*:\/\/([^/?#]+)/i)
  const host = (m ? m[1] : url.split(/[/?#]/)[0]).toLowerCase()
  const cdnHosts = [
    'xhscdn.com',
    'douyinpic.com',
    'douyinvod.com',
    'pstatp.com',
    'snssdk.com',
    'ixigua.com',
  ]
  return !cdnHosts.some((h) => host === h || host.endsWith('.' + h))
}

export default function DetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const { apiBaseUrl, toggleFavorite, fetchInspirations } = useInspirationStore()

  /** 全屏图片查看器开关（点大图打开） */
  const [viewerOpen, setViewerOpen] = useState(false)
  /** 标签编辑弹层开关 */
  const [tagEditorOpen, setTagEditorOpen] = useState(false)
  const [tagGroups, setTagGroups] = useState<TagCategoryGroup[]>([])
  const [tagGroupsLoading, setTagGroupsLoading] = useState(false)
  const [savingTags, setSavingTags] = useState(false)
  /** 标签库是否加载失败（弱网/后端未启动时给重试入口） */
  const [tagLoadError, setTagLoadError] = useState(false)
  /** 「更多」弹层（删除收在这里，P2-9） */
  const [moreOpen, setMoreOpen] = useState(false)
  /** 保存成功的轻提示 */
  const [snackbar, setSnackbar] = useState('')
  const [snackbarVisible, setSnackbarVisible] = useState(false)

  const [detail, setDetail] = useState<Inspiration | null>(null)
  const [similar, setSimilar] = useState<Inspiration[]>([])
  const [loading, setLoading] = useState(true)
  /** 错误文案：404 显示「素材未找到」，网络/服务错误单独提示 */
  const [error, setError] = useState('')

  const loadDetail = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const { data } = await apiClient.get<Inspiration>(`/inspirations/${id}`)
      setDetail(data)
    } catch (e) {
      setDetail(null)
      const status = (e as { response?: { status?: number } })?.response?.status
      setError(status === 404 ? '素材未找到' : '加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
    }
  }, [id])

  /** 相似推荐：失败不影响主内容（向量库可能没建好） */
  const loadSimilar = useCallback(async () => {
    try {
      const items = await fetchSimilarInspirations(String(id))
      setSimilar(items.filter((i) => i.id !== id).slice(0, 12))
    } catch {
      setSimilar([])
    }
  }, [id])

  useEffect(() => {
    void loadDetail()
    void loadSimilar()
  }, [loadDetail, loadSimilar])

  const showSnack = (message: string) => {
    setSnackbar(message)
    setSnackbarVisible(true)
  }

  const handleToggleFav = async () => {
    if (!detail) return
    const newState = !detail.is_favorite
    try {
      await toggleFavorite(String(id), newState)
      setDetail({ ...detail, is_favorite: newState })
      successFeedback()
    } catch {
      // 失败时保持原状态并提示，避免 UI 与服务端不一致
      Alert.alert('操作失败', '收藏状态更新失败，请稍后重试')
    }
  }

  const handleOpenSource = async () => {
    if (!detail?.source_url) return
    try {
      await Linking.openURL(detail.source_url)
    } catch {
      // 打开失败（无效链接/未安装浏览器）静默处理
    }
  }

  /** 拉取标签库（失败时留在弹层里给「重试」，不弹 Alert 打断） */
  const loadTagGroups = async () => {
    setTagGroupsLoading(true)
    setTagLoadError(false)
    try {
      setTagGroups(await fetchTagGroups())
    } catch {
      setTagLoadError(true)
    } finally {
      setTagGroupsLoading(false)
    }
  }

  /** 打开标签编辑：按需拉取标签库 */
  const openTagEditor = async () => {
    setTagEditorOpen(true)
    if (tagGroups.length > 0 || tagGroupsLoading) return
    await loadTagGroups()
  }

  /** 统一提交标签：先删后增（一次保存 = 一次确认，P1-8） */
  const handleSubmitTags = async ({ add, remove }: { add: string[]; remove: number[] }) => {
    if (add.length === 0 && remove.length === 0) {
      setTagEditorOpen(false)
      return
    }
    setSavingTags(true)
    try {
      for (const tagId of remove) {
        await removeTagFromInspiration(String(id), tagId)
      }
      if (add.length > 0) {
        await addTagsToInspiration(String(id), add)
      }
      setTagEditorOpen(false)
      await loadDetail()
      successFeedback()
      const parts: string[] = []
      if (add.length > 0) parts.push(`新增 ${add.length} 个`)
      if (remove.length > 0) parts.push(`移除 ${remove.length} 个`)
      showSnack(`标签已保存（${parts.join('，')}）`)
    } catch {
      warningFeedback()
      Alert.alert('保存失败', '标签保存失败，请稍后重试')
    } finally {
      setSavingTags(false)
    }
  }

  /** 移入垃圾桶（软删除，可在 Web 端恢复） */
  const handleTrash = () => {
    setMoreOpen(false)
    Alert.alert('移入垃圾桶', '素材会移入垃圾桶（可恢复），确定吗？', [
      { text: '取消', style: 'cancel' },
      {
        text: '移入',
        style: 'destructive',
        onPress: async () => {
          try {
            await trashInspiration(String(id))
            // 列表里要去掉这条，回上一页时不会显示已删素材
            await fetchInspirations()
            router.back()
          } catch {
            Alert.alert('操作失败', '移入垃圾桶失败，请稍后重试')
          }
        },
      },
    ])
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <SkeletonBlock style={styles.mainImageSkeleton} />
        <View style={styles.skeletonTexts}>
          <SkeletonBlock style={styles.skeletonLine} />
          <SkeletonBlock style={styles.skeletonLineShort} />
        </View>
      </View>
    )
  }

  if (!detail) {
    return (
      <View style={styles.container}>
        <ErrorState
          title={error || '素材未找到'}
          description={error === '素材未找到' ? '这条素材可能已被删除' : '请检查网络或后端服务'}
          onRetry={() => void loadDetail()}
        />
      </View>
    )
  }

  // 按类别分组标签
  const groupedTags: Record<string, InspirationTag[]> = {}
  detail.tags?.forEach((t) => {
    const cat = t.tag.category
    if (!groupedTags[cat]) groupedTags[cat] = []
    groupedTags[cat].push(t)
  })

  // 视频素材没有可渲染的 <Image> 源：用导入时生成的**首帧缩略图**当封面
  // （直接把 mp4 路径交给 Image 会是一片空白，全屏浏览同样白屏）
  const isVideo = detail.media_type === 'video'
  const coverUri = getFileUrl(
    apiBaseUrl,
    isVideo ? detail.thumbnail_path || detail.file_path : detail.file_path,
  )
  // 主色兜底：加载中/letterbox 都不再是死灰色（P0-8）
  const letterbox = dominantColor(detail.dominant_colors) ?? theme.color.bgSubtle

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* 视频：内嵌播放器（封面帧 + 原生控件 + 全屏）；图片：点开全屏看图 */}
        {isVideo ? (
          <VideoPlayer uri={getFileUrl(apiBaseUrl, detail.file_path)} posterUri={coverUri} />
        ) : (
          <DetailImage
            uri={coverUri}
            letterbox={letterbox}
            onOpen={() => setViewerOpen(true)}
          />
        )}

        {/* 操作栏：删除收进「更多」（P2-9） */}
        <View style={styles.actions}>
          {!detail.deleted_at ? (
            <ActionItem
              icon={detail.is_favorite ? 'heart' : 'heart-outline'}
              label={detail.is_favorite ? '已收藏' : '收藏'}
              color={detail.is_favorite ? theme.color.dangerText : theme.color.textSecondary}
              onPress={() => void handleToggleFav()}
            />
          ) : null}
          <ActionItem
            icon="pricetag-outline"
            label="编辑标签"
            color={theme.color.brandText}
            onPress={() => void openTagEditor()}
          />
          {detail.source_url && isSourceLinkValid(detail.source_url) ? (
            <ActionItem
              icon="link-outline"
              label="原链接"
              color={theme.color.brandText}
              onPress={() => void handleOpenSource()}
            />
          ) : null}
          <ActionItem
            icon="ellipsis-horizontal"
            label="更多"
            color={theme.color.textSecondary}
            onPress={() => setMoreOpen(true)}
          />
        </View>

        {/* 元信息 */}
        <View style={styles.metaSection}>
          <MetaRow label="来源" value={sourceLabel(detail.source_type)} />
          {detail.source_author ? <MetaRow label="作者" value={`@${detail.source_author}`} /> : null}
          <MetaRow label="时间" value={new Date(detail.created_at).toLocaleDateString('zh-CN')} />
          {isVideo ? <MetaRow label="类型" value="视频" /> : null}
        </View>

        {/* 标签 */}
        {detail.tags && detail.tags.length > 0 ? (
          <View style={styles.tagsSection}>
            <View style={styles.sectionHeader}>
              <AppText variant="subtitle">标签</AppText>
              <Pressable onPress={() => void openTagEditor()} hitSlop={10} accessibilityRole="button">
                <AppText variant="bodySm" color="brandText">
                  编辑
                </AppText>
              </Pressable>
            </View>
            {Object.entries(groupedTags).map(([category, tags]) => (
              <View key={category} style={styles.tagGroup}>
                <AppText variant="caption" color="textTertiary">
                  {tagCategoryLabel(category)}
                </AppText>
                <View style={styles.tagChips}>
                  {tags.map((t) => (
                    <View key={t.tag.id} style={styles.tagChip}>
                      <AppText variant="bodySm" color="brandText" numberOfLines={1}>
                        {t.tag.name}
                        {t.confidence < 0.8 ? ` ${Math.round(t.confidence * 100)}%` : ''}
                      </AppText>
                    </View>
                  ))}
                </View>
              </View>
            ))}
          </View>
        ) : (
          <Pressable
            style={styles.noTags}
            onPress={() => void openTagEditor()}
            accessibilityRole="button"
          >
            <Icon name="pricetags-outline" size={theme.icon.xl} color={theme.color.textDisabled} />
            <AppText variant="bodySm" color="textTertiary">
              暂无标签，点这里手动添加
            </AppText>
          </Pressable>
        )}

        {/* 相似素材 */}
        <SimilarList
          items={similar}
          resolveUri={(item) => getFileUrl(apiBaseUrl, item.thumbnail_path || item.file_path)}
          onPress={(nextId) => router.push(`/detail/${nextId}`)}
        />
      </ScrollView>

      {!isVideo ? (
        <ImageViewer visible={viewerOpen} uri={coverUri} onClose={() => setViewerOpen(false)} />
      ) : null}

      {/* 标签编辑弹层（保存时统一提交新增+移除） */}
      <TagEditor
        visible={tagEditorOpen}
        loading={tagGroupsLoading}
        groups={tagGroups}
        current={detail.tags ?? []}
        onClose={() => setTagEditorOpen(false)}
        onSubmit={handleSubmitTags}
        saving={savingTags}
        loadError={tagLoadError}
        onRetry={() => void loadTagGroups()}
      />

      {/* 更多操作 */}
      <BottomSheet visible={moreOpen} title="更多操作" onClose={() => setMoreOpen(false)}>
        <Pressable
          style={styles.moreRow}
          onPress={handleTrash}
          accessibilityRole="button"
          testID="trash-action"
        >
          <Icon name="trash-outline" size={theme.icon.md} color={theme.color.dangerText} />
          <AppText variant="body" color="dangerText">
            移入垃圾桶
          </AppText>
        </Pressable>
        <View style={styles.moreHint}>
          <AppText variant="caption" color="textTertiary">
            移入垃圾桶后可恢复，素材不会立即删除
          </AppText>
        </View>
      </BottomSheet>

      <Snackbar
        visible={snackbarVisible}
        message={snackbar}
        onHide={() => setSnackbarVisible(false)}
      />
    </View>
  )
}

/** 详情主图：按真实比例 + 主色 letterbox + 淡入 + 全屏入口（P0-8） */
function DetailImage({
  uri,
  letterbox,
  onOpen,
}: {
  uri: string
  letterbox: string
  onOpen: () => void
}) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const { ratio, onLoad } = useAspectRatio(uri, FALLBACK_RATIO)
  const { width } = useWindowDimensions()
  // 高度自己算（而不是 aspectRatio + maxHeight）：否则竖图被钳高后宽度跟着收窄、整块靠左
  const height = detailImageHeight(ratio, width, MAX_IMAGE_HEIGHT)
  return (
    <Pressable onPress={onOpen} accessibilityRole="imagebutton" accessibilityLabel="全屏查看">
      <Image
        source={{ uri }}
        onLoad={onLoad}
        style={[styles.mainImage, { height, backgroundColor: letterbox }]}
        contentFit="contain"
        transition={200}
        cachePolicy="memory-disk"
      />
      <View style={styles.zoomBadge}>
        <Icon name="expand-outline" size={theme.icon.sm} color={theme.color.textOnMedia} />
        <AppText variant="micro" color="textOnMedia">
          全屏
        </AppText>
      </View>
    </Pressable>
  )
}

/** 操作栏单项（图标 + 文案，整块 44 高可点） */
function ActionItem({
  icon,
  label,
  color,
  onPress,
}: {
  icon: string
  label: string
  color: string
  onPress: () => void
}) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [styles.actionItem, pressed && styles.pressed]}
    >
      <Icon name={icon} size={theme.icon.lg} color={color} />
      <AppText variant="caption" color="textSecondary">
        {label}
      </AppText>
    </Pressable>
  )
}

/** 元信息行：左 13px 标签 / 右 15px 值（§4.3） */
function MetaRow({ label, value }: { label: string; value: string }) {
  const styles = useStyles(makeStyles)
  return (
    <View style={styles.metaRow}>
      <AppText variant="bodySm" color="textTertiary">
        {label}
      </AppText>
      <AppText variant="body" numberOfLines={1} style={styles.metaValue}>
        {value}
      </AppText>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgSurface },
    content: { paddingBottom: t.space.xxxl },
    mainImage: { width: '100%' },
    mainImageSkeleton: { width: '100%', aspectRatio: 0.8 },
    skeletonTexts: { padding: t.space.lg, gap: t.space.sm },
    skeletonLine: { height: 16, width: '60%' },
    skeletonLineShort: { height: 12, width: '35%' },
    zoomBadge: {
      position: 'absolute',
      right: t.space.md,
      bottom: t.space.md,
      flexDirection: 'row',
      alignItems: 'center',
      gap: 3,
      paddingHorizontal: t.space.sm,
      paddingVertical: 3,
      borderRadius: t.radius.badge,
      backgroundColor: t.color.scrim,
    },
    actions: {
      flexDirection: 'row',
      paddingVertical: t.space.sm,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    actionItem: { flex: 1, minHeight: t.touch, alignItems: 'center', justifyContent: 'center', gap: 2 },
    pressed: { opacity: 0.7 },
    metaSection: {
      padding: t.space.lg,
      gap: t.space.sm,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.color.divider,
    },
    metaRow: { flexDirection: 'row', alignItems: 'center', gap: t.space.md },
    metaValue: { flex: 1, textAlign: 'right' },
    tagsSection: { padding: t.space.lg, gap: t.space.md },
    sectionHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
    tagGroup: { gap: t.space.sm },
    tagChips: { flexDirection: 'row', flexWrap: 'wrap', gap: t.space.sm },
    tagChip: {
      maxWidth: '100%',
      paddingHorizontal: t.space.md,
      paddingVertical: t.space.xs,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.brandSubtle,
    },
    noTags: { alignItems: 'center', paddingVertical: t.space.xxxl, gap: t.space.sm },
    moreRow: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
    },
    moreHint: { paddingHorizontal: t.space.lg, paddingBottom: t.space.md },
  })
