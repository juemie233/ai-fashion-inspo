/** 出图批次详情（只读）：候选图网格 + 点开全屏预览。
 *
 * 审查修复：三态（骨架/错误+重试/空态）、候选图统一 `MediaCard`（1:1）、
 * 已入库角标用 Badge。
 */

import { useCallback, useEffect, useState } from 'react'
import { FlatList, StyleSheet, View } from 'react-native'
import { useLocalSearchParams } from 'expo-router'
import MediaCard from '../../components/ui/MediaCard'
import EmptyState from '../../components/ui/EmptyState'
import ErrorState from '../../components/ui/ErrorState'
import AppText from '../../components/ui/AppText'
import { SkeletonGrid } from '../../components/ui/Skeleton'
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
import { useStyles } from '../../hooks/useTheme'
import type { Theme } from '../../constants/theme'

/** 缩略图宽度（后端支持 ?w= 生成缩略图，省流量） */
const THUMB_WIDTH = 400

export default function GenerateRunScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const styles = useStyles(makeStyles)
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
      <View style={styles.container}>
        <SkeletonGrid count={4} columns={2} ratio={1} />
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={candidates}
        keyExtractor={(c) => c.filename}
        numColumns={2}
        columnWrapperStyle={styles.row}
        contentContainerStyle={styles.grid}
        ListHeaderComponent={
          <View style={styles.header}>
            <AppText variant="subtitle" numberOfLines={2}>
              {modelLabel(run?.model_key, modelLabels)}
            </AppText>
            <AppText variant="caption" color="textSecondary" numberOfLines={4}>
              {run?.prompt || '（无描述）'}
            </AppText>
            {run?.negative ? (
              <AppText variant="micro" color="textTertiary" numberOfLines={2}>
                负面词：{run.negative}
              </AppText>
            ) : null}
            <AppText variant="caption" color="textTertiary" tabularNums>
              {run?.done ?? candidates.length}/{run?.total ?? candidates.length} 张
              {run?.imported_count ? ` · 已入库 ${run.imported_count}` : ''}
            </AppText>
          </View>
        }
        renderItem={({ item }) => (
          <MediaCard
            testID={`candidate-${item.index}`}
            uri={candidateUrl(apiBaseUrl, item.url, THUMB_WIDTH)}
            ratio={1}
            cornerBadge={item.imported ? '已入库' : undefined}
            onPress={() => setViewerUri(candidateUrl(apiBaseUrl, item.url))}
          />
        )}
        ListEmptyComponent={
          error ? (
            <ErrorState onRetry={() => void load()} />
          ) : (
            <EmptyState icon="images-outline" title="这个批次没有候选图" />
          )
        }
      />
      <ImageViewer
        visible={viewerUri !== null}
        uri={viewerUri ?? ''}
        onClose={() => setViewerUri(null)}
      />
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    row: { gap: t.space.md, marginBottom: t.space.md },
    header: { marginBottom: t.space.md, gap: t.space.xs },
  })
