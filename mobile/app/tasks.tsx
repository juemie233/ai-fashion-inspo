/** 后台任务页：只看进度与失败原因，能取消；发起任务仍在上传/采集页。
 *
 * 设计取舍（见 docs/mobile-porting-plan.md）：暂停/恢复/删除留在 Web ——
 * 状态机复杂，手机上误触代价高。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import { cancelTask, fetchTasks, type TaskItem } from '../services/api'
import TaskRow from '../components/TaskRow'

export default function TasksScreen() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [cancellingId, setCancellingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchTasks(1, 30)
      setTasks(Array.isArray(data?.items) ? data.items : [])
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

  /** 是否有排队/进行中的任务：有就自动刷新，没就停（避免空转） */
  const hasActive = tasks.some((t) => t.status === 'pending' || t.status === 'running')

  useEffect(() => {
    if (!hasActive) return
    // 从上传页点「查看分析进度」进来就是为了看进度，只靠下拉刷新的话进度条不会动
    const timer = setInterval(() => {
      void load()
    }, 3000)
    return () => clearInterval(timer)
  }, [hasActive, load])

  const handleRefresh = () => {
    setRefreshing(true)
    void load()
  }

  const handleCancel = (id: number) => {
    Alert.alert('取消任务', '确定要取消这个任务吗？', [
      { text: '再想想', style: 'cancel' },
      {
        text: '确定取消',
        style: 'destructive',
        onPress: async () => {
          setCancellingId(id)
          try {
            await cancelTask(id)
            await load()
          } catch {
            Alert.alert('取消失败', '请稍后重试')
          } finally {
            setCancellingId(null)
          }
        },
      },
    ])
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
      data={tasks}
      keyExtractor={(t) => String(t.id)}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#6366f1" />
      }
      renderItem={({ item }) => (
        <TaskRow task={item} onCancel={handleCancel} cancelling={cancellingId === item.id} />
      )}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyText}>{error || '暂无后台任务'}</Text>
          {error ? <Text style={styles.emptyHint}>下拉可重试</Text> : null}
        </View>
      }
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  list: { paddingVertical: 12 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#f9fafb' },
  empty: { paddingTop: 80, alignItems: 'center', gap: 6 },
  emptyText: { fontSize: 14, color: '#9ca3af' },
  emptyHint: { fontSize: 12, color: '#d1d5db' },
})
