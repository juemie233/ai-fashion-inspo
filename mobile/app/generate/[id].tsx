/** 出图批次详情（只读）：候选图网格 + 点开全屏预览。 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Image,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useLocalSearchParams } from 'expo-router'
import Icon from '../../components/Icon'
import ImageViewer from '../../components/ImageViewer'
import {
  candidateUrl,
  fetchGenerateModelLabels,
  fetchGenerateRun,
  type GenerateCandidate,
  type GenerateRun,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { modelLabel } from '../../utils/modelLabel'

/** 缩略图宽度（后端支持 ?w= 生成缩略图，省流量） */
const THUMB_WIDTH = 400

export default function GenerateRunScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)
  const runId = Number(id)

  const [run, setRun] = useState<GenerateRun | null>(null)
  const [candidates, setCandidates] = useState<GenerateCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 全屏预览的图片地址 */
  const [viewerUri, setViewerUri] = useState<string | null>(null)
  /** 后端给的最新档位展示名（拉不到时用内置兜底） */
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    if (!Number.isFinite(runId)) {
      setError('批次不存在')
      setLoading(false)
      return
    }
    try {
      const detail = await fetchGenerateRun(runId)
      setRun(detail.run)
      setCandidates(detail.candidates)
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    // 档位名从后端取（单一来源）；失败不打断详情，回退内置表
    let alive = true
    void fetchGenerateModelLabels()
      .then((labels) => {
        if (alive) setModelLabels(labels)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#6366f1" />
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={candidates}
      keyExtractor={(c) => c.filename}
      numColumns={2}
      columnWrapperStyle={styles.row}
      contentContainerStyle={styles.grid}
      ListHeaderComponent={
        <View style={styles.header}>
          <Text style={styles.model}>
            {modelLabel(run?.model_key, modelLabels)}
          </Text>
          <Text style={styles.prompt}>{run?.prompt || '（无描述）'}</Text>
          {run?.negative ? <Text style={styles.negative}>负面词：{run.negative}</Text> : null}
          <Text style={styles.meta}>
            {run?.done ?? candidates.length}/{run?.total ?? candidates.length} 张
            {run?.imported_count ? ` · 已入库 ${run.imported_count}` : ''}
          </Text>
        </View>
      }
      renderItem={({ item }) => (
        <TouchableOpacity
          testID={`candidate-${item.index}`}
          style={styles.cell}
          activeOpacity={0.85}
          onPress={() => setViewerUri(candidateUrl(apiBaseUrl, item.url))}
        >
          <Image
            source={{ uri: candidateUrl(apiBaseUrl, item.url, THUMB_WIDTH) }}
            style={styles.image}
          />
          {item.imported ? (
            <View style={styles.importedBadge}>
              <Icon name="checkmark" size={11} color="#fff" />
              <Text style={styles.importedText}>已入库</Text>
            </View>
          ) : null}
        </TouchableOpacity>
      )}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Icon name="images-outline" size={48} color="#d1d5db" />
          <Text style={styles.emptyText}>{error || '这个批次没有候选图'}</Text>
        </View>
      }
      ListFooterComponent={
        <ImageViewer
          visible={viewerUri !== null}
          uri={viewerUri ?? ''}
          onClose={() => setViewerUri(null)}
        />
      }
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#f9fafb' },
  grid: { padding: 12, paddingBottom: 24 },
  row: { gap: 12, marginBottom: 12 },
  header: { marginBottom: 12, gap: 6 },
  model: { fontSize: 15, fontWeight: '600', color: '#111827' },
  prompt: { fontSize: 12, color: '#4b5563', lineHeight: 18 },
  negative: { fontSize: 11, color: '#9ca3af' },
  meta: { fontSize: 11, color: '#9ca3af' },
  cell: { flex: 1, aspectRatio: 1, borderRadius: 10, overflow: 'hidden', backgroundColor: '#fff' },
  image: { width: '100%', height: '100%', backgroundColor: '#f3f4f6' },
  importedBadge: {
    position: 'absolute',
    left: 6,
    bottom: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: 'rgba(22,163,74,0.9)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
  },
  importedText: { fontSize: 10, color: '#fff' },
  empty: { paddingTop: 40, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 13, color: '#9ca3af' },
})
