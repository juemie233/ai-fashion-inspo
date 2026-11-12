/** 合集宫格页：一级合集（封面 + 名称 + 数量），支持新建 / 改名 / 上移下移 / 删除。
 *
 * 手机侧取舍：**不做拖拽排序**（触屏与滚动冲突），用「上移 / 下移」替代；
 * 不做智能合集的规则编辑（见 docs/mobile/移动端.md）。
 *
 * P0-4：从「只读」升级为可维护——
 * - 新建：页内工具条「新建合集」；
 * - 改名 / 上移 / 下移 / 删除：长按合集卡片打开操作弹层；
 * - 排序走 `PATCH /api/collections/order`（提交完整的一级合集顺序，与 Web 一致）；
 * - 删除有二次确认，并明确「只删合集，不动素材」。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native'
import { Image } from 'expo-image'
import { useRouter } from 'expo-router'
import Icon from '../components/Icon'
import AppText from '../components/ui/AppText'
import Badge from '../components/ui/Badge'
import Button from '../components/ui/Button'
import BottomSheet from '../components/ui/BottomSheet'
import EmptyState from '../components/ui/EmptyState'
import ErrorState from '../components/ui/ErrorState'
import { SkeletonGrid } from '../components/ui/Skeleton'
import {
  createCollection,
  deleteCollection,
  fetchCollections,
  getFileUrl,
  reorderCollections,
  updateCollection,
  type CollectionItem,
} from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'
import { useStyles, useTheme } from '../hooks/useTheme'
import { successFeedback, warningFeedback } from '../utils/haptics'
import { gridColumns } from '../utils/layout'
import type { Theme } from '../constants/theme'

export default function CollectionsScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const apiBaseUrl = useInspirationStore((s) => s.apiBaseUrl)
  const { width } = useWindowDimensions()
  /** 平板/大屏切 3 列 */
  const columns = gridColumns(width)

  const [collections, setCollections] = useState<CollectionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  /** 长按打开的操作弹层目标 */
  const [actionItem, setActionItem] = useState<CollectionItem | null>(null)
  /** 正在改名的合集（改名弹层用，与操作弹层分开：打开改名时操作弹层要关掉） */
  const [renameTarget, setRenameTarget] = useState<CollectionItem | null>(null)
  /** 新建/改名弹层 */
  const [editorMode, setEditorMode] = useState<'create' | 'rename' | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  /** 正在提交（禁重复点击） */
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const list = await fetchCollections()
      // 只展示一级合集；二级收藏夹在合集详情里体现
      setCollections(list.filter((c) => !c.parent_id))
      setError('')
    } catch {
      setError('加载失败，请检查网络或后端服务')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const onRefresh = () => {
    setRefreshing(true)
    void load()
  }

  /** 打开「新建」 */
  const openCreate = () => {
    setNameDraft('')
    setEditorMode('create')
  }

  /** 打开「改名」 */
  const openRename = (item: CollectionItem) => {
    setActionItem(null)
    setRenameTarget(item)
    setNameDraft(item.name)
    setEditorMode('rename')
  }

  /** 提交新建 / 改名 */
  const submitEditor = async () => {
    const name = nameDraft.trim()
    if (!name) {
      Alert.alert('请填写名称', '合集名不能为空')
      return
    }
    setBusy(true)
    try {
      if (editorMode === 'create') {
        await createCollection(name)
      } else if (renameTarget) {
        await updateCollection(renameTarget.id, { name })
      }
      setEditorMode(null)
      setRenameTarget(null)
      setActionItem(null)
      successFeedback()
      await load()
    } catch (e) {
      warningFeedback()
      const detail = (e as { response?: { status?: number } })?.response?.status
      Alert.alert(
        editorMode === 'create' ? '新建失败' : '改名失败',
        detail === 409 ? '同名合集已存在，换个名字试试' : '请稍后重试',
      )
    } finally {
      setBusy(false)
    }
  }

  /**
   * 上移 / 下移：把该项与相邻项交换后提交完整顺序。
   *
   * 为什么不提交「只动这两条」：后端 `PATCH /collections/order` 的语义是
   * 「按 ordered_ids 重排，未出现的追加到末尾」，提交完整列表才与 Web 的拖拽结果一致。
   */
  const move = async (item: CollectionItem, direction: -1 | 1) => {
    const index = collections.findIndex((c) => c.id === item.id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= collections.length) return
    setActionItem(null)

    const next = [...collections]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved)
    // 先本地生效（点一下就有反馈），失败再回滚
    setCollections(next)
    try {
      await reorderCollections(next.map((c) => c.id))
      successFeedback()
    } catch {
      warningFeedback()
      setCollections(collections)
      Alert.alert('排序失败', '请检查网络或后端服务')
    }
  }

  /** 删除（二次确认，明确不动素材） */
  const confirmDelete = (item: CollectionItem) => {
    setActionItem(null)
    Alert.alert('删除合集', `确定删除「${item.name}」吗？合集里的素材不会被删除。`, [
      { text: '取消', style: 'cancel' },
      {
        text: '删除',
        style: 'destructive',
        onPress: async () => {
          setBusy(true)
          try {
            await deleteCollection(item.id)
            setCollections((prev) => prev.filter((c) => c.id !== item.id))
            successFeedback()
          } catch {
            warningFeedback()
            Alert.alert('删除失败', '请稍后重试')
          } finally {
            setBusy(false)
          }
        },
      },
    ])
  }

  const actionIndex = actionItem
    ? collections.findIndex((c) => c.id === actionItem.id)
    : -1
  const canMoveUp = actionIndex > 0
  const canMoveDown = actionIndex >= 0 && actionIndex < collections.length - 1

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
        testID="collections-list"
        style={styles.list}
        data={collections}
        keyExtractor={(c) => String(c.id)}
        numColumns={columns}
        key={`collections-${columns}`}
        columnWrapperStyle={styles.row}
        contentContainerStyle={styles.grid}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            colors={[theme.color.brandFill]}
            tintColor={theme.color.brandFill}
          />
        }
        ListHeaderComponent={
          <View style={styles.toolbar}>
            <Button
              title="新建合集"
              icon="add"
              variant="secondary"
              onPress={openCreate}
              testID="collection-create"
            />
            <AppText variant="caption" color="textTertiary">
              长按合集可改名 / 排序 / 删除
            </AppText>
          </View>
        }
        renderItem={({ item }) => (
          <Pressable
            testID={`collection-${item.id}`}
            style={({ pressed }) => [styles.card, pressed && styles.pressed]}
            onPress={() => router.push(`/collection/${item.id}`)}
            onLongPress={() => setActionItem(item)}
            delayLongPress={280}
            accessibilityRole="button"
            accessibilityLabel={`合集：${item.name}`}
          >
            {item.cover_thumbnail_path ? (
              <Image
                source={{ uri: getFileUrl(apiBaseUrl, item.cover_thumbnail_path) }}
                style={styles.cover}
                contentFit="cover"
                transition={150}
                cachePolicy="memory-disk"
              />
            ) : (
              <View style={[styles.cover, styles.coverEmpty]}>
                <Icon name="albums-outline" size={theme.icon.xl} color={theme.color.textDisabled} />
              </View>
            )}
            <View style={styles.meta}>
              <AppText variant="subtitle" numberOfLines={1}>
                {item.name}
              </AppText>
              {item.kind === 'smart' ? (
                <Badge text="智能合集" tone="brand" />
              ) : (
                <AppText variant="caption" color="textTertiary" tabularNums>
                  {item.item_count ?? 0} 张
                </AppText>
              )}
            </View>
          </Pressable>
        )}
        ListEmptyComponent={
          error ? (
            <ErrorState onRetry={() => void load()} />
          ) : (
            <EmptyState
              icon="albums-outline"
              title="还没有合集"
              description="点上方「新建合集」，或在素材库长按素材加合集"
            />
          )
        }
      />

      {/* 长按操作弹层 */}
      <BottomSheet
        visible={actionItem !== null}
        title={actionItem?.name ?? ''}
        onClose={() => setActionItem(null)}
        testID="collection-actions"
        footer={
          <>
            <Button title="取消" variant="secondary" onPress={() => setActionItem(null)} />
          </>
        }
      >
        <Pressable
          testID="collection-rename"
          style={({ pressed }) => [styles.actionRow, pressed && styles.pressed]}
          onPress={() => actionItem && openRename(actionItem)}
          disabled={busy}
          accessibilityRole="button"
        >
          <Icon name="create-outline" size={theme.icon.md} color={theme.color.brandText} />
          <AppText variant="body">改名</AppText>
        </Pressable>
        <Pressable
          testID="collection-move-up"
          style={({ pressed }) => [
            styles.actionRow,
            !canMoveUp && styles.actionDisabled,
            pressed && canMoveUp ? styles.pressed : null,
          ]}
          onPress={() => actionItem && void move(actionItem, -1)}
          disabled={!canMoveUp || busy}
          accessibilityRole="button"
          accessibilityState={{ disabled: !canMoveUp }}
        >
          <Icon name="arrow-up-outline" size={theme.icon.md} color={theme.color.textSecondary} />
          <AppText variant="body" color={canMoveUp ? 'textPrimary' : 'textDisabled'}>
            上移
          </AppText>
        </Pressable>
        <Pressable
          testID="collection-move-down"
          style={({ pressed }) => [
            styles.actionRow,
            !canMoveDown && styles.actionDisabled,
            pressed && canMoveDown ? styles.pressed : null,
          ]}
          onPress={() => actionItem && void move(actionItem, 1)}
          disabled={!canMoveDown || busy}
          accessibilityRole="button"
          accessibilityState={{ disabled: !canMoveDown }}
        >
          <Icon name="arrow-down-outline" size={theme.icon.md} color={theme.color.textSecondary} />
          <AppText variant="body" color={canMoveDown ? 'textPrimary' : 'textDisabled'}>
            下移
          </AppText>
        </Pressable>
        <Pressable
          testID="collection-delete"
          style={({ pressed }) => [styles.actionRow, pressed && styles.pressed]}
          onPress={() => actionItem && confirmDelete(actionItem)}
          disabled={busy}
          accessibilityRole="button"
        >
          <Icon name="trash-outline" size={theme.icon.md} color={theme.color.dangerText} />
          <AppText variant="body" color="dangerText">
            删除合集
          </AppText>
        </Pressable>
        <AppText variant="caption" color="textTertiary" style={styles.actionHint}>
          删除合集不会删除里面的素材
        </AppText>
      </BottomSheet>

      {/* 新建 / 改名弹层 */}
      <BottomSheet
        visible={editorMode !== null}
        title={editorMode === 'create' ? '新建合集' : '合集改名'}
        onClose={() => setEditorMode(null)}
        testID="collection-editor"
        footer={
          <>
            <Button title="取消" variant="secondary" onPress={() => setEditorMode(null)} disabled={busy} />
            <Button
              title={editorMode === 'create' ? '创建' : '保存'}
              onPress={() => void submitEditor()}
              loading={busy}
              disabled={!nameDraft.trim()}
              testID="collection-editor-submit"
            />
          </>
        }
      >
        <View style={styles.editorBody}>
          <TextInput
            testID="collection-name-input"
            style={styles.input}
            value={nameDraft}
            onChangeText={setNameDraft}
            placeholder="合集名称（最多 50 字）"
            placeholderTextColor={theme.color.textTertiary}
            maxLength={50}
            autoFocus
            autoCorrect={false}
          />
        </View>
      </BottomSheet>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    list: { flex: 1 },
    toolbar: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingBottom: t.space.md,
    },
    grid: { padding: t.space.md, paddingBottom: t.space.xxl, flexGrow: 1 },
    row: { gap: t.space.md, marginBottom: t.space.md },
    card: {
      flex: 1,
      maxWidth: '48%',
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      overflow: 'hidden',
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
    },
    pressed: { opacity: 0.85 },
    cover: { width: '100%', aspectRatio: 1, backgroundColor: t.color.bgSubtle },
    coverEmpty: { alignItems: 'center', justifyContent: 'center' },
    meta: { padding: t.space.md, gap: t.space.xs },
    actionRow: {
      minHeight: 52,
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingHorizontal: t.space.lg,
    },
    actionDisabled: { opacity: 0.5 },
    actionHint: { paddingHorizontal: t.space.lg, paddingBottom: t.space.sm },
    editorBody: { paddingHorizontal: t.space.lg, paddingBottom: t.space.md },
    input: {
      minHeight: t.touch,
      borderWidth: 1,
      borderColor: t.color.border,
      borderRadius: t.radius.control,
      paddingHorizontal: t.space.md,
      fontSize: t.font.body.fontSize,
      color: t.color.textPrimary,
    },
  })
