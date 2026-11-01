/** 采集页：拍照 / 相册多选 → 队列逐张上传（每张独立进度与失败重试）。
 *
 * 与 Web 的差别（见 docs/移动端.md）：去掉拖拽/粘贴，保留相册多选与
 * 逐张进度；上传完可一键跳到「后台任务」看 AI 分析进度。
 *
 * 审查修复：
 * - P0-5 移除按钮从 20×20 改成 44 触控区；失败项不再在缩略图上写 9px 正文
 * - P2-4 队列细节：3:4 比例（不再 1:1 裁人像）、失败项点开看错误详情并重试、
 *   「清空已完成」「全部重试」、上传总进度 + 取消、成功弹窗只留有效动作
 */

import { useRef, useState } from 'react'
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, View } from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import { useRouter } from 'expo-router'
import { Image } from 'expo-image'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import ScreenHeader from '../../components/ui/ScreenHeader'
import Icon from '../../components/Icon'
import AppText from '../../components/ui/AppText'
import Button from '../../components/ui/Button'
import IconButton from '../../components/ui/IconButton'
import Badge from '../../components/ui/Badge'
import BottomSheet from '../../components/ui/BottomSheet'
import EmptyState from '../../components/ui/EmptyState'
import { useStyles, useTheme } from '../../hooks/useTheme'
import { uploadInspirationImage, type Inspiration } from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import { successFeedback, warningFeedback } from '../../utils/haptics'
import type { Theme } from '../../constants/theme'

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
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const insets = useSafeAreaInsets()
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [uploading, setUploading] = useState(false)
  /** 上传中取消标志（点「取消」后剩下的不再上传） */
  const cancelRef = useRef(false)
  /** 失败项详情弹层 */
  const [errorItem, setErrorItem] = useState<QueueItem | null>(null)
  const store = useInspirationStore()

  const addUris = (uris: string[]) => {
    // 超限提示放在 updater **外面**：state updater 必须是纯函数（StrictMode 下会被
    // 调用两次，写在里面会弹两次窗，也违反 React 的约定）
    const existing = new Set(queue.map((q) => q.uri))
    const added = uris
      .filter((u) => !existing.has(u))
      .map<QueueItem>((uri) => ({ uri, status: 'pending', progress: 0 }))
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

    cancelRef.current = false
    setUploading(true)
    let ok = 0
    let failed = 0

    for (const item of targets) {
      if (cancelRef.current) break
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

    const cancelled = cancelRef.current
    cancelRef.current = false
    setUploading(false)

    if (cancelled) {
      Alert.alert('已取消上传', `已完成 ${ok} 张，未上传的可以稍后再传`)
      return
    }
    if (ok > 0 && failed === 0) {
      successFeedback()
      Alert.alert('上传完成', `${ok} 张已入库，AI 将自动分析标签`, [
        { text: '完成', style: 'cancel' },
        { text: '查看任务', onPress: () => router.push('/tasks') },
      ])
    } else if (ok > 0 || failed > 0) {
      warningFeedback()
      Alert.alert('部分失败', `成功 ${ok} 张，失败 ${failed} 张，点失败的可以看原因并重试`)
    }
  }

  const handleCancelUpload = () => {
    cancelRef.current = true
  }

  const pendingCount = queue.filter((q) => q.status === 'pending' || q.status === 'error').length
  const doneCount = queue.filter((q) => q.status === 'done').length
  const errorCount = queue.filter((q) => q.status === 'error').length
  const uploadingItem = queue.find((q) => q.status === 'uploading')
  const totalCount = queue.filter((q) => q.status !== 'pending').length
  const overallRatio = queue.length > 0 ? doneCount / queue.length : 0

  return (
    <View style={styles.container}>
      <ScreenHeader title="采集" />

      {queue.length === 0 ? (
        <View style={styles.emptyArea}>
          <EmptyState
            icon="images-outline"
            title="相册可多选、也可直接拍"
            description={`一次最多 ${MAX_BATCH} 张，上传后自动 AI 打标`}
            actionLabel="从相册选择"
            onAction={() => void pickFromGallery()}
          />
          <Button
            title="拍照"
            icon="camera-outline"
            variant="secondary"
            onPress={() => void takePhoto()}
          />
        </View>
      ) : (
        <FlatList
          data={queue}
          numColumns={3}
          keyExtractor={(q) => q.uri}
          contentContainerStyle={styles.grid}
          columnWrapperStyle={styles.gridRow}
          renderItem={({ item }) => (
            <View style={styles.cell}>
              <Pressable
                style={styles.cellPress}
                onPress={() => (item.status === 'error' ? setErrorItem(item) : undefined)}
                accessibilityRole={item.status === 'error' ? 'button' : undefined}
                accessibilityLabel={item.status === 'error' ? '查看失败原因' : undefined}
              >
                <Image
                  source={{ uri: item.uri }}
                  style={styles.thumb}
                  contentFit="cover"
                  transition={100}
                />
              </Pressable>

              {item.status === 'uploading' ? (
                <View style={styles.cellOverlay}>
                  <ActivityIndicator color={theme.color.textOnMedia} />
                  <AppText variant="micro" color="textOnMedia" tabularNums>
                    {Math.round(item.progress * 100)}%
                  </AppText>
                </View>
              ) : null}

              {item.status === 'done' ? (
                <View style={styles.cellBadge}>
                  <Icon name="checkmark" size={theme.icon.sm} color={theme.color.textOnMedia} />
                </View>
              ) : null}

              {item.status === 'error' ? (
                <>
                  <View style={[styles.cellBadge, styles.cellBadgeError]}>
                    <Icon name="alert" size={theme.icon.sm} color={theme.color.textOnMedia} />
                  </View>
                  <View pointerEvents="none" style={styles.cellErrorBorder} />
                </>
              ) : null}

              {item.status !== 'uploading' ? (
                <IconButton
                  name="close"
                  size={theme.icon.sm}
                  color={theme.color.textOnMedia}
                  background="scrim"
                  style={styles.cellRemove}
                  onPress={() => removeAt(item.uri)}
                  accessibilityLabel="移除这张"
                />
              ) : null}
            </View>
          )}
        />
      )}

      {/* 统计条 + 批量动作（P2-4） */}
      {queue.length > 0 ? (
        <View style={styles.summary}>
          <AppText variant="caption" color="textSecondary" tabularNums>
            已选 {queue.length} · 已完成 {doneCount} · 失败 {errorCount}
          </AppText>
          <View style={styles.summaryActions}>
            {doneCount > 0 ? (
              <Pressable
                onPress={() => setQueue((prev) => prev.filter((q) => q.status !== 'done'))}
                hitSlop={10}
                accessibilityRole="button"
              >
                <AppText variant="caption" color="brandText">
                  清空已完成
                </AppText>
              </Pressable>
            ) : null}
            {pendingCount > 0 ? (
              <Pressable onPress={() => void handleUploadAll()} hitSlop={10} accessibilityRole="button">
                <AppText variant="caption" color="brandText">
                  全部重试
                </AppText>
              </Pressable>
            ) : null}
          </View>
        </View>
      ) : null}

      {/* 上传总进度 + 取消 */}
      {uploading ? (
        <View style={styles.progressRow}>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: `${Math.round(overallRatio * 100)}%` }]} />
          </View>
          <AppText variant="caption" color="textTertiary" tabularNums>
            {doneCount}/{queue.length}
            {uploadingItem ? ' 上传中' : ''}
          </AppText>
          <Button title="取消" variant="ghost" onPress={handleCancelUpload} />
        </View>
      ) : null}

      {/* 操作栏 */}
      <View style={[styles.actions, { paddingBottom: insets.bottom + 8 }]}>
        <Pressable style={styles.actionBtn} onPress={() => void takePhoto()} disabled={uploading}>
          <View style={styles.actionIcon}>
            <Icon name="camera" size={theme.icon.lg} color={theme.color.textOnMedia} />
          </View>
          <AppText variant="caption" color="textSecondary">
            拍照
          </AppText>
        </Pressable>

        <Pressable style={styles.actionBtn} onPress={() => void pickFromGallery()} disabled={uploading}>
          <View style={styles.actionIcon}>
            <Icon name="images" size={theme.icon.lg} color={theme.color.textOnMedia} />
          </View>
          <AppText variant="caption" color="textSecondary">
            相册多选
          </AppText>
        </Pressable>

        <Button
          title={uploading ? '上传中…' : pendingCount > 0 ? `上传 ${pendingCount} 张` : '上传入库'}
          onPress={() => void handleUploadAll()}
          disabled={pendingCount === 0 || uploading}
          loading={uploading}
          style={styles.uploadBtn}
          testID="upload-button"
        />
      </View>

      {/* 失败详情 */}
      <BottomSheet
        visible={Boolean(errorItem)}
        title="上传失败"
        onClose={() => setErrorItem(null)}
        footer={
          <>
            <Button title="关闭" variant="secondary" onPress={() => setErrorItem(null)} />
            <Button
              title="重试这张"
              onPress={() => {
                const target = errorItem
                setErrorItem(null)
                if (target) {
                  updateItem(target.uri, { status: 'pending', error: undefined })
                  void handleUploadAll()
                }
              }}
            />
          </>
        }
      >
        <View style={styles.errorBox}>
          <AppText variant="body" color="dangerText">
            {errorItem?.error ?? '未知错误'}
          </AppText>
          <AppText variant="caption" color="textTertiary">
            常见原因：后端未启动、手机与电脑不在同一 Wi-Fi、图片格式不支持。
          </AppText>
        </View>
      </BottomSheet>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    emptyArea: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: t.space.md },
    grid: { padding: t.space.md, gap: t.space.xs },
    gridRow: { gap: t.space.xs },
    // 3 列等宽 + 3:4 比例：maxWidth 防止最后一行只有 1 张时被拉宽（P2-4）
    cell: {
      flex: 1,
      maxWidth: '32.5%',
      aspectRatio: 0.75,
      borderRadius: t.radius.control,
      overflow: 'hidden',
    },
    cellPress: { flex: 1 },
    thumb: { width: '100%', height: '100%', backgroundColor: t.color.bgSubtle },
    cellOverlay: {
      ...StyleSheet.absoluteFillObject,
      backgroundColor: t.color.scrim,
      alignItems: 'center',
      justifyContent: 'center',
      gap: t.space.xs,
    },
    cellBadge: {
      position: 'absolute',
      left: t.space.xs,
      bottom: t.space.xs,
      width: 22,
      height: 22,
      borderRadius: t.radius.pill,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: t.color.successFill,
    },
    cellBadgeError: { backgroundColor: t.color.dangerFill },
    cellErrorBorder: {
      ...StyleSheet.absoluteFillObject,
      borderWidth: 2,
      borderColor: t.color.dangerFill,
      borderRadius: t.radius.control,
    },
    // 44×44 触控区（P0-5：改前 20×20 很难点中）
    cellRemove: { position: 'absolute', top: 0, right: 0 },
    summary: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.divider,
    },
    summaryActions: { flexDirection: 'row', alignItems: 'center', gap: t.space.md },
    progressRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.sm,
      paddingHorizontal: t.space.lg,
      paddingVertical: t.space.sm,
      backgroundColor: t.color.bgSurface,
    },
    progressTrack: {
      flex: 1,
      height: 4,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.bgSubtle,
      overflow: 'hidden',
    },
    progressFill: { height: '100%', backgroundColor: t.color.brandFill },
    actions: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: t.space.lg,
      paddingTop: t.space.md,
      gap: t.space.md,
      backgroundColor: t.color.bgSurface,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.color.divider,
    },
    actionBtn: { alignItems: 'center', gap: t.space.xs },
    actionIcon: {
      width: 48,
      height: 48,
      borderRadius: t.radius.pill,
      alignItems: 'center',
      justifyContent: 'center',
      backgroundColor: t.color.brandFill,
    },
    uploadBtn: { flex: 1 },
    errorBox: { paddingHorizontal: t.space.lg, paddingBottom: t.space.md, gap: t.space.sm },
  })
