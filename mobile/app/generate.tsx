/** 出图记录（只读）：批次列表。
 *
 * 手机侧取舍（docs/移动端.md）：出图依赖 PC 上的 ComfyUI，手机上不做参数
 * 面板与提交，只看「出了什么、要不要用」——点进批次可以看候选图与全屏预览。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import Icon from '../components/Icon'
import { fetchGenerateModelLabels, fetchGenerateRuns, type GenerateRun } from '../services/api'
import { modelLabel } from '../utils/modelLabel'

const PAGE_SIZE = 20

/** 批次状态中文名与配色（枚举与后端 generation.GENERATION_STATUSES 对齐） */
const STATUS_META: Record<string, { label: string; color: string }> = {
  queued: { label: '排队中', color: '#f59e0b' },
  waiting_vram: { label: '等待显存', color: '#f59e0b' },
  running: { label: '出图中', color: '#6366f1' },
  done: { label: '已完成', color: '#16a34a' },
  failed: { label: '失败', color: '#ef4444' },
  cancelled: { label: '已取消', color: '#9ca3af' },
}

export default function GenerateScreen() {
  const router = useRouter()

  const [runs, setRuns] = useState<GenerateRun[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  /** 后端给的最新档位展示名（拉不到时用内置兜底） */
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({})

  /** 翻页互斥 + 请求序号：慢网重复触发 onEndReached 时不再追加重复页 */
  const loadingMoreRef = useRef(false)
  const requestSeqRef = useRef(0)

  const load = useCallback(async (nextPage = 1) => {
    if (nextPage > 1 && loadingMoreRef.current) return
    const seq = ++requestSeqRef.current
    if (nextPage > 1) loadingMoreRef.current = true
    try {
      const data = await fetchGenerateRuns(nextPage, PAGE_SIZE)
      if (seq !== requestSeqRef.current) return
      setRuns((prev) => (nextPage === 1 ? data.items : [...prev, ...data.items]))
      setTotal(data.total)
      setPage(nextPage)
      setError('')
    } catch {
      if (seq === requestSeqRef.current) setError('加载失败，请检查网络或后端服务')
    } finally {
      if (nextPage > 1) loadingMoreRef.current = false
      if (seq === requestSeqRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    // 档位名从后端取（单一来源）；失败不打断列表，回退内置表
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

  useEffect(() => {
    void load(1)
  }, [load])

  const onRefresh = () => {
    setRefreshing(true)
    void load(1)
  }

  const onEndReached = () => {
    if (!loading && runs.length < total) void load(page + 1)
  }

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
      data={runs}
      keyExtractor={(r) => String(r.id)}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      onEndReached={onEndReached}
      onEndReachedThreshold={0.5}
      renderItem={({ item }) => {
        const status = STATUS_META[item.status] ?? { label: item.status, color: '#9ca3af' }
        return (
          <TouchableOpacity
            testID={`run-${item.id}`}
            style={styles.card}
            activeOpacity={0.85}
            onPress={() => router.push(`/generate/${item.id}`)}
          >
            <View style={styles.cardHeader}>
              <Text style={styles.model} numberOfLines={1}>
                {modelLabel(item.model_key, modelLabels)}
              </Text>
              <Text style={[styles.status, { color: status.color }]}>{status.label}</Text>
            </View>

            <Text style={styles.prompt} numberOfLines={2}>
              {item.prompt || '（无描述）'}
            </Text>

            <View style={styles.footer}>
              <Text style={styles.meta}>
                {item.done}/{item.total} 张
                {item.imported_count > 0 ? ` · 已入库 ${item.imported_count}` : ''}
                {item.files_missing ? ' · 文件缺失' : ''}
              </Text>
              <Text style={styles.time}>{new Date(item.created_at).toLocaleDateString('zh-CN')}</Text>
            </View>
          </TouchableOpacity>
        )
      }}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Icon name="sparkles-outline" size={56} color="#d1d5db" />
          <Text style={styles.emptyText}>{error || '还没有出图记录'}</Text>
          <Text style={styles.emptyHint}>出图在电脑端进行，手机上看结果</Text>
        </View>
      }
      ListFooterComponent={
        <Text style={styles.footerText}>{total > 0 ? `共 ${total} 个批次` : ''}</Text>
      }
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#f9fafb' },
  list: { padding: 12, paddingBottom: 24 },
  card: { backgroundColor: '#fff', borderRadius: 10, padding: 12, marginBottom: 8 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  model: { flex: 1, fontSize: 13, fontWeight: '600', color: '#111827' },
  status: { fontSize: 12 },
  prompt: { fontSize: 12, color: '#4b5563', marginTop: 6, lineHeight: 17 },
  footer: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 },
  meta: { fontSize: 11, color: '#9ca3af' },
  time: { fontSize: 11, color: '#d1d5db' },
  empty: { paddingTop: 80, alignItems: 'center', gap: 8 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
  emptyHint: { fontSize: 12, color: '#d1d5db' },
  footerText: { textAlign: 'center', fontSize: 11, color: '#d1d5db', paddingVertical: 12 },
})
