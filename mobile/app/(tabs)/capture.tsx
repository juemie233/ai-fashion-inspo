/** 采集页：拍照 / 相册多选 → 队列逐张上传（每张独立进度与失败重试）。
 *
 * 与 Web 的差别（见 docs/移动端.md）：去掉拖拽/粘贴，保留相册多选与
 * 逐张进度；上传完可一键跳到「后台任务」看 AI 分析进度。
 */

import { useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import { useRouter } from 'expo-router'
import Icon from '../../components/Icon'
import { uploadInspirationImage, type Inspiration } from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'

/** 队列项状态：pending 待传 / uploading 上传中 / done 成功 / error 失败 */
type QueueStatus = 'pending' | 'uploading' | 'done' | 'error'

interface QueueItem {
  /** 本地临时 URI（用作 key 与缩略图） */
  uri: string
  status: QueueStatus
  /** 0~1 上传进度 */
  progress: number
  error?: string
}

/** 单次最多选多少张：太多会让队列长时间跑，手机息屏会断 */
const MAX_BATCH = 20

export default function CaptureScreen() {
  const router = useRouter()
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [uploading, setUploading] = useState(false)
  const store = useInspirationStore()

  const addUris = (uris: string[]) => {
    // 超限提示放在 updater **外面**：state updater 必须是纯函数（StrictMode 下会被
    // 调用两次，写在里面会弹两次窗，也违反 React 的约定）
    const existing = new Set(queue.map((q) => q.uri))
    const added = uris.filter((u) => !existing.has(u)).map<QueueItem>((uri) => ({
      uri,
      status: 'pending',
      progress: 0,
    }))
    const next = [...queue, ...added]
    if (next.length > MAX_BATCH) {
      Alert.alert('超出上限', `一次最多 ${MAX_BATCH} 张，多余的已忽略`)
      setQueue(next.slice(0, MAX_BATCH))
      return
    }
    setQueue(next)
  }

  /** 从相册多选 */
  const pickFromGallery = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在系统设置中允许访问相册')
      return
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.9,
      allowsMultipleSelection: true,
      selectionLimit: MAX_BATCH,
    })

    if (!result.canceled && result.assets?.length) {
      addUris(result.assets.map((a) => a.uri))
    }
  }

  /** 拍照 */
  const takePhoto = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在系统设置中允许使用相机')
      return
    }

    const result = await ImagePicker.launchCameraAsync({ quality: 0.9 })
    if (!result.canceled && result.assets?.[0]) {
      addUris([result.assets[0].uri])
    }
  }

  const removeAt = (uri: string) => {
    setQueue((prev) => prev.filter((q) => q.uri !== uri))
  }

  const updateItem = (uri: string, patch: Partial<QueueItem>) => {
    setQueue((prev) => prev.map((q) => (q.uri === uri ? { ...q, ...patch } : q)))
  }

  /** 串行上传：一次一张，避免局域网下并发把带宽打满、也让进度更可读 */
  const handleUploadAll = async () => {
    const targets = queue.filter((q) => q.status === 'pending' || q.status === 'error')
    if (targets.length === 0) return

    setUploading(true)
    let ok = 0
    let failed = 0

    for (const item of targets) {
      updateItem(item.uri, { status: 'uploading', progress: 0, error: undefined })
      try {
        const created: Inspiration = await uploadInspirationImage(item.uri, (ratio) => {
          updateItem(item.uri, { progress: ratio })
        })
        updateItem(item.uri, { status: 'done', progress: 1 })
        store.prependItem(created)
        ok += 1
      } catch (e) {
        const message = e instanceof Error ? e.message : '上传失败'
        updateItem(item.uri, { status: 'error', progress: 0, error: message })
        failed += 1
      }
    }

    setUploading(false)
    if (ok > 0 && failed === 0) {
      Alert.alert('上传完成', `${ok} 张已入库，AI 将自动分析标签`, [
        { text: '继续上传' },
        { text: '查看任务', onPress: () => router.push('/tasks') },
      ])
    } else if (ok > 0 || failed > 0) {
      Alert.alert('部分失败', `成功 ${ok} 张，失败 ${failed} 张，可点失败的重新上传`)
    }
  }

  const pendingCount = queue.filter((q) => q.status === 'pending' || q.status === 'error').length
  const doneCount = queue.filter((q) => q.status === 'done').length

  return (
    <View style={styles.container}>
      {/* 队列区 */}
      {queue.length === 0 ? (
        <View style={styles.emptyArea}>
          <Icon name="images-outline" size={72} color="#d1d5db" />
          <Text style={styles.emptyText}>相册可多选、也可直接拍</Text>
          <Text style={styles.emptyHint}>一次最多 {MAX_BATCH} 张</Text>
        </View>
      ) : (
        <FlatList
          data={queue}
          numColumns={3}
          keyExtractor={(q) => q.uri}
          contentContainerStyle={styles.grid}
          renderItem={({ item }) => (
            <View style={styles.cell}>
              <Image source={{ uri: item.uri }} style={styles.thumb} />

              {item.status === 'uploading' && (
                <View style={styles.cellOverlay}>
                  <ActivityIndicator color="#fff" />
                  <Text style={styles.cellOverlayText}>{Math.round(item.progress * 100)}%</Text>
                </View>
              )}

              {item.status === 'done' && (
                <View style={[styles.cellBadge, { backgroundColor: '#16a34a' }]}>
                  <Icon name="checkmark" size={12} color="#fff" />
                </View>
              )}

              {item.status === 'error' && (
                <View style={[styles.cellBadge, { backgroundColor: '#ef4444' }]}>
                  <Icon name="alert" size={12} color="#fff" />
                </View>
              )}

              {item.status !== 'uploading' && (
                <TouchableOpacity
                  style={styles.cellRemove}
                  onPress={() => removeAt(item.uri)}
                  hitSlop={6}
                >
                  <Icon name="close" size={12} color="#fff" />
                </TouchableOpacity>
              )}

              {item.status === 'error' && item.error ? (
                <Text style={styles.cellError} numberOfLines={1}>
                  {item.error}
                </Text>
              ) : null}
            </View>
          )}
        />
      )}

      {/* 统计条 */}
      {queue.length > 0 && (
        <View style={styles.summary}>
          <Text style={styles.summaryText}>
            共 {queue.length} 张 · 已上传 {doneCount} · 待上传 {pendingCount}
          </Text>
          {doneCount > 0 && (
            <TouchableOpacity onPress={() => router.push('/tasks')} hitSlop={6}>
              <Text style={styles.linkText}>查看分析进度</Text>
            </TouchableOpacity>
          )}
        </View>
      )}

      {/* 操作栏 */}
      <View style={styles.actions}>
        <TouchableOpacity style={styles.actionBtn} onPress={takePhoto} disabled={uploading}>
          <View style={[styles.actionIcon, { backgroundColor: '#6366f1' }]}>
            <Icon name="camera" size={26} color="#fff" />
          </View>
          <Text style={styles.actionLabel}>拍照</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.actionBtn} onPress={pickFromGallery} disabled={uploading}>
          <View style={[styles.actionIcon, { backgroundColor: '#8b5cf6' }]}>
            <Icon name="images" size={26} color="#fff" />
          </View>
          <Text style={styles.actionLabel}>相册多选</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.uploadBtn, (pendingCount === 0 || uploading) && styles.uploadBtnDisabled]}
          onPress={handleUploadAll}
          disabled={pendingCount === 0 || uploading}
        >
          <Text style={styles.uploadBtnText}>
            {uploading ? '上传中…' : pendingCount > 0 ? `上传 ${pendingCount} 张` : '上传入库'}
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  emptyArea: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  emptyText: { fontSize: 15, color: '#9ca3af' },
  emptyHint: { fontSize: 12, color: '#d1d5db' },
  grid: { padding: 12, gap: 6 },
  cell: { flex: 1 / 3, margin: 3, aspectRatio: 1, borderRadius: 8, overflow: 'hidden' },
  thumb: { width: '100%', height: '100%', backgroundColor: '#e5e7eb' },
  cellOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.45)',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  cellOverlayText: { color: '#fff', fontSize: 12 },
  cellBadge: {
    position: 'absolute',
    left: 4,
    bottom: 4,
    width: 20,
    height: 20,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cellRemove: {
    position: 'absolute',
    right: 4,
    top: 4,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  cellError: { position: 'absolute', left: 0, right: 0, bottom: 0, fontSize: 9, color: '#fff', backgroundColor: 'rgba(239,68,68,0.85)', paddingHorizontal: 4 },
  summary: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  summaryText: { fontSize: 12, color: '#6b7280' },
  linkText: { fontSize: 12, color: '#6366f1', fontWeight: '500' },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    gap: 12,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  actionBtn: { alignItems: 'center', gap: 4 },
  actionIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionLabel: { fontSize: 11, color: '#6b7280' },
  uploadBtn: {
    flex: 1,
    backgroundColor: '#6366f1',
    paddingVertical: 15,
    borderRadius: 10,
    alignItems: 'center',
    marginLeft: 'auto',
  },
  uploadBtnDisabled: { backgroundColor: '#c7d2fe' },
  uploadBtnText: { color: '#fff', fontSize: 15, fontWeight: '600' },
})
