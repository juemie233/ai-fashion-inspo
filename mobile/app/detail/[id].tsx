/** 素材详情页：大图浏览 + 全屏看图 + 标签编辑 + 移入垃圾桶 + 相似推荐。 */

import { useState, useEffect, useCallback } from 'react'
import {
  View,
  Text,
  Image,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Dimensions,
  Linking,
  Alert,
} from 'react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'
import Icon from '../../components/Icon'
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
import { sourceLabel } from '../../utils/sourceLabel'
import ImageViewer from '../../components/ImageViewer'
import SimilarList from '../../components/SimilarList'
import TagEditor from '../../components/TagEditor'

const { width } = Dimensions.get('window')

/** 标签分类中文名 */
const CAT_LABELS: Record<string, string> = {
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

  const handleToggleFav = async () => {
    if (!detail) return
    const newState = !detail.is_favorite
    try {
      await toggleFavorite(String(id), newState)
      setDetail({ ...detail, is_favorite: newState })
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

  const handleAddTags = async (names: string[]) => {
    if (names.length === 0) {
      setTagEditorOpen(false)
      return
    }
    setSavingTags(true)
    try {
      await addTagsToInspiration(String(id), names)
      setTagEditorOpen(false)
      await loadDetail()
    } catch {
      Alert.alert('保存失败', '标签保存失败，请稍后重试')
    } finally {
      setSavingTags(false)
    }
  }

  const handleRemoveTag = async (tagId: number) => {
    setSavingTags(true)
    try {
      await removeTagFromInspiration(String(id), tagId)
      await loadDetail()
    } catch {
      Alert.alert('移除失败', '请稍后重试')
    } finally {
      setSavingTags(false)
    }
  }

  /** 移入垃圾桶（软删除，可在 Web 端恢复） */
  const handleTrash = () => {
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
      <View style={styles.loading}>
        <ActivityIndicator size="large" color="#6366f1" />
      </View>
    )
  }

  if (!detail) {
    return (
      <View style={styles.loading}>
        <Text style={styles.noTagsText}>{error || '素材未找到'}</Text>
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

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ paddingBottom: 40 }}>
      {/* 大图：点击进入全屏浏览 */}
      <TouchableOpacity activeOpacity={0.9} onPress={() => setViewerOpen(true)}>
        <Image source={{ uri: coverUri }} style={styles.mainImage} />
        {isVideo && (
          <View style={styles.videoBadge} pointerEvents="none">
            <Icon name="videocam-outline" size={14} color="#fff" />
            <Text style={styles.zoomBadgeText}>视频封面</Text>
          </View>
        )}
        <View style={styles.zoomBadge} pointerEvents="none">
          <Icon name="expand-outline" size={14} color="#fff" />
          <Text style={styles.zoomBadgeText}>全屏</Text>
        </View>
      </TouchableOpacity>

      <ImageViewer visible={viewerOpen} uri={coverUri} onClose={() => setViewerOpen(false)} />

      {/* 操作栏 */}
      <View style={styles.actions}>
        <TouchableOpacity style={styles.actionItem} onPress={handleToggleFav}>
          <Icon
            name={detail.is_favorite ? 'heart' : 'heart-outline'}
            size={22}
            color={detail.is_favorite ? '#ef4444' : '#6b7280'}
          />
          <Text style={styles.actionText}>{detail.is_favorite ? '已收藏' : '收藏'}</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.actionItem} onPress={openTagEditor}>
          <Icon name="pricetag-outline" size={22} color="#6366f1" />
          <Text style={[styles.actionText, { color: '#6366f1' }]}>编辑标签</Text>
        </TouchableOpacity>

        {detail.source_url && isSourceLinkValid(detail.source_url) && (
          <TouchableOpacity style={styles.actionItem} onPress={handleOpenSource}>
            <Icon name="link-outline" size={22} color="#6366f1" />
            <Text style={[styles.actionText, { color: '#6366f1' }]}>原链接</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity style={styles.actionItem} onPress={handleTrash}>
          <Icon name="trash-outline" size={22} color="#ef4444" />
          <Text style={[styles.actionText, { color: '#ef4444' }]}>删除</Text>
        </TouchableOpacity>
      </View>

      {/* 元信息 */}
      <View style={styles.metaSection}>
        <View style={styles.metaRow}>
          <Text style={styles.metaLabel}>来源</Text>
          <Text style={styles.metaValue}>{sourceLabel(detail.source_type)}</Text>
        </View>
        {detail.source_author && (
          <View style={styles.metaRow}>
            <Text style={styles.metaLabel}>作者</Text>
            <Text style={styles.metaValue}>@{detail.source_author}</Text>
          </View>
        )}
        <View style={styles.metaRow}>
          <Text style={styles.metaLabel}>时间</Text>
          <Text style={styles.metaValue}>
            {new Date(detail.created_at).toLocaleDateString('zh-CN')}
          </Text>
        </View>
      </View>

      {/* 标签 */}
      {detail.tags && detail.tags.length > 0 ? (
        <View style={styles.tagsSection}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>标签</Text>
            <TouchableOpacity onPress={openTagEditor} hitSlop={8}>
              <Text style={styles.editLink}>编辑</Text>
            </TouchableOpacity>
          </View>
          {Object.entries(groupedTags).map(([category, tags]) => (
            <View key={category} style={styles.tagGroup}>
              <Text style={styles.tagCategoryLabel}>{CAT_LABELS[category] || category}</Text>
              <View style={styles.tagChips}>
                {tags.map((t) => (
                  <View key={t.tag.id} style={styles.tagChip}>
                    <Text style={styles.tagChipText}>
                      {t.tag.name}
                      {t.confidence < 0.8 ? ` (${Math.round(t.confidence * 100)}%)` : ''}
                    </Text>
                  </View>
                ))}
              </View>
            </View>
          ))}
        </View>
      ) : (
        <TouchableOpacity style={styles.noTags} onPress={openTagEditor}>
          <Icon name="pricetags-outline" size={32} color="#d1d5db" />
          <Text style={styles.noTagsText}>暂无标签，点这里手动添加</Text>
        </TouchableOpacity>
      )}

      {/* 相似素材 */}
      <SimilarList
        items={similar}
        resolveUri={(item) => getFileUrl(apiBaseUrl, item.thumbnail_path || item.file_path)}
        onPress={(nextId) => router.push(`/detail/${nextId}`)}
      />

      {/* 标签编辑弹层 */}
      <TagEditor
        visible={tagEditorOpen}
        loading={tagGroupsLoading}
        groups={tagGroups}
        current={detail.tags ?? []}
        onClose={() => setTagEditorOpen(false)}
        onAdd={handleAddTags}
        onRemove={handleRemoveTag}
        saving={savingTags}
        loadError={tagLoadError}
        onRetry={() => void loadTagGroups()}
      />
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
  },
  mainImage: {
    width: width,
    height: width * 1.25,
    backgroundColor: '#f3f4f6',
    resizeMode: 'contain',
  },
  actions: {
    flexDirection: 'row',
    paddingVertical: 12,
    paddingHorizontal: 8,
    gap: 4,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  actionItem: { flex: 1, alignItems: 'center', gap: 4 },
  actionText: { fontSize: 11, color: '#6b7280' },
  metaSection: {
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  metaRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  metaLabel: { fontSize: 14, color: '#9ca3af' },
  metaValue: { fontSize: 14, color: '#1f2937' },
  tagsSection: { padding: 16 },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#1f2937' },
  editLink: { fontSize: 13, color: '#6366f1' },
  tagGroup: { marginBottom: 14 },
  tagCategoryLabel: {
    fontSize: 12,
    color: '#9ca3af',
    marginBottom: 6,
  },
  tagChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tagChip: {
    backgroundColor: '#eef2ff',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  tagChipText: { fontSize: 13, color: '#6366f1' },
  noTags: {
    alignItems: 'center',
    padding: 40,
    gap: 8,
  },
  noTagsText: { fontSize: 14, color: '#9ca3af' },
  lastRow: { borderBottomWidth: 0 },
  videoBadge: {
    position: 'absolute',
    left: 10,
    bottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  zoomBadge: {
    position: 'absolute',
    right: 10,
    bottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: 'rgba(0,0,0,0.5)',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  zoomBadgeText: { color: '#fff', fontSize: 11 },
})
