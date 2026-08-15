<script setup lang="ts">
/** 高级素材管理页：统计仪表盘、存储分析、完整性检查、批量操作。 */

import { ref, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { formatSize } from '@/utils/format'
// 来源类型中文映射（此前去重已落地，供后台来源列展示，保留 import）
import { sourceLabel } from '@/utils/sourceLabel'
import { useAdminTask } from '@/composables/useAdminTask'
import type { Stats, LargeFile, MissingFile, OrphanFile, DuplicateGroup, DedupResult } from '@/types/admin'

import AdminStatCards from '@/components/admin/AdminStatCards.vue'
import AdminProblemCards from '@/components/admin/AdminProblemCards.vue'
import AdminTaskProgress from '@/components/admin/AdminTaskProgress.vue'
import AdminDistStats from '@/components/admin/AdminDistStats.vue'
import AdminLargeFiles from '@/components/admin/AdminLargeFiles.vue'
import AdminIntegrityCheck from '@/components/admin/AdminIntegrityCheck.vue'
import AdminDuplicates from '@/components/admin/AdminDuplicates.vue'

const message = useMessage()

// ── 响应式状态 ──

const stats = ref<Stats | null>(null)
const largestFiles = ref<LargeFile[]>([])
const missingFiles = ref<MissingFile[]>([])
const orphanFiles = ref<OrphanFile[]>([])
const orphanSize = ref(0)
const duplicates = ref<DuplicateGroup[]>([])
const dupCount = ref(0)
const dupSize = ref(0)
const loading = ref(true)
const checking = ref(false)

// ── 批量删除 ──
const clearingUntagged = ref(false)
const clearingFailed = ref(false)
const deduplicating = ref(false)
const dedupResult = ref<DedupResult | null>(null)

// ── 后台任务轮询（批量删除/去重）──
const { adminTask, startAdminPolling, stopAdminPolling, resumeAdminTask } = useAdminTask()

/** 后台任务完成后的统一处理：根据任务类型刷新统计并提示 */
function handleAdminTaskDone() {
  const task = adminTask.value
  const r = task?.result
  if (task?.type === 'deduplicate') {
    dedupResult.value = r
      ? { groups_processed: r.groups_processed ?? 0, files_deleted: r.files_deleted ?? 0, freed_bytes: r.freed_bytes ?? 0 }
      : null
    if (!r || r.files_deleted === 0) {
      message.info('未找到可删除的重复文件')
    } else {
      message.success(`去重完成：处理 ${r.groups_processed ?? 0} 组，删除 ${r.files_deleted ?? 0} 个冗余文件，释放 ${formatSize(r.freed_bytes ?? 0)} 空间`)
    }
    loadDuplicates()
  } else {
    const label = r?.label === 'untagged' ? '无标签素材' : r?.label === 'analysis_failed' ? '分析失败素材' : '素材'
    message.success(`已删除 ${r?.deleted_count ?? 0} 个${label}，释放 ${formatSize(r?.freed_bytes ?? 0)} 空间`)
  }
  adminTask.value = null
  loadAll()
}

// ── 数据加载 ──

async function loadAll() {
  loading.value = true
  try {
    const [sRes, lRes] = await Promise.all([
      apiClient.get('/admin/stats'),
      apiClient.get('/admin/largest-files?limit=20'),
    ])
    stats.value = sRes.data
    largestFiles.value = lRes.data
  } catch (e: any) {
    message.error('加载统计数据失败')
  } finally {
    loading.value = false
  }
}

async function loadIntegrity() {
  checking.value = true
  try {
    const res = await apiClient.get('/admin/integrity-check')
    missingFiles.value = res.data.missing_files
    orphanFiles.value = res.data.orphan_files
    orphanSize.value = res.data.orphan_total_size_bytes
  } catch (e: any) {
    message.error('完整性检查失败')
  } finally {
    checking.value = false
  }
}

async function loadDuplicates() {
  checking.value = true
  try {
    const res = await apiClient.get('/admin/duplicates')
    duplicates.value = res.data.duplicate_groups
    dupCount.value = res.data.duplicate_count
    dupSize.value = res.data.wasted_bytes
  } catch (e: any) {
    message.error('重复检测失败')
  } finally {
    checking.value = false
  }
}

/** 手动检测重复：先清掉上次的去重结果提示，再加载重复列表 */
function scanDuplicates() {
  dedupResult.value = null
  loadDuplicates()
}

// ── 去重删除 ──

async function deduplicate() {
  deduplicating.value = true
  dedupResult.value = null
  try {
    const { data } = await apiClient.post<{ message: string; task_id: number }>('/admin/deduplicate')
    adminTask.value = { id: data.task_id, type: 'deduplicate', status: 'pending', progress: 0, total: 0, done: 0, result: null, error: null }
    message.success(data.message)
    startAdminPolling(data.task_id, () => handleAdminTaskDone())
  } catch (e: any) {
    message.error(e.response?.data?.detail || '去重删除失败')
  } finally {
    deduplicating.value = false
  }
}

// ── 清理孤立文件 ──

async function cleanOrphans() {
  try {
    const res = await apiClient.post('/admin/cleanup-orphans')
    message.success(`已删除 ${res.data.deleted_count} 个孤立文件，释放 ${formatSize(res.data.freed_bytes)} 空间`)
    await loadIntegrity()
    await loadAll()  // 顶部统计（存储总大小/来源分布）同步刷新
  } catch (e: any) {
    message.error('清理失败')
  }
}

// ── 批量删除 ──

async function batchDeleteByCondition(condition: string) {
  try {
    if (condition === 'untagged') clearingUntagged.value = true
    else clearingFailed.value = true
    const { data } = await apiClient.post<{ message: string; task_id: number }>('/admin/batch-delete', { condition })
    adminTask.value = { id: data.task_id, type: 'batch_delete', status: 'pending', progress: 0, total: 0, done: 0, result: null, error: null }
    message.success(data.message)
    startAdminPolling(data.task_id, () => handleAdminTaskDone())
  } catch (e: any) {
    message.error('批量删除失败')
  } finally {
    clearingUntagged.value = false
    clearingFailed.value = false
  }
}

// ── 生命周期 ──

onMounted(() => {
  loadAll()
  resumeAdminTask(() => handleAdminTaskDone())
})

onUnmounted(() => {
  stopAdminPolling()
})
</script>

<template>
  <div class="admin-page">
    <h2>素材管理</h2>
    <p class="subtitle">统计、审计、清理和批量操作</p>

    <!-- ====== 概览卡片 ====== -->
    <admin-stat-cards :stats="stats" />

    <!-- ====== 后台任务进度 ====== -->
    <admin-task-progress :task="adminTask" />

    <!-- ====== 问题概览卡片 ====== -->
    <admin-problem-cards
      :stats="stats"
      :clearing-untagged="clearingUntagged"
      :clearing-failed="clearingFailed"
      @delete-untagged="batchDeleteByCondition('untagged')"
      @delete-failed="batchDeleteByCondition('analysis_failed')"
    />

    <!-- ====== 分布统计 ====== -->
    <admin-dist-stats :stats="stats" />

    <!-- ====== 最大文件 ====== -->
    <admin-large-files :files="largestFiles" />

    <!-- ====== 完整性检查 ====== -->
    <admin-integrity-check
      :missing-files="missingFiles"
      :orphan-files="orphanFiles"
      :orphan-bytes="orphanSize"
      :checking="checking"
      @recheck="loadIntegrity"
      @clean-orphans="cleanOrphans"
    />

    <!-- ====== 重复检测 ====== -->
    <admin-duplicates
      :duplicates="duplicates"
      :dup-count="dupCount"
      :dup-bytes="dupSize"
      :checking="checking"
      :deduplicating="deduplicating"
      :dedup-result="dedupResult"
      @scan="scanDuplicates"
      @deduplicate="deduplicate"
    />

    <!-- ====== 提示 ====== -->
    <p style="color: #999; font-size: 12px">
      💡 提示：定期检查数据完整性和重复文件，可以保持素材库健康。建议每月执行一次。
    </p>
  </div>
</template>
<style scoped>
.admin-page {
  max-width: 1100px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 24px;
}
</style>
