<script setup lang="ts">
/** 数据治理页：批量清理、数据完整性、重复文件、近似重复（素材管理的治理类功能拆分）。 */

import { ref, onMounted, onUnmounted, watch } from 'vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { useRouter, useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { formatSize } from '@/utils/format'
import { useAdminTask } from '@/composables/useAdminTask'
import type {
  Stats,
  LargeFile,
  MissingFile,
  OrphanFile,
  DuplicateGroup,
  DedupResult,
} from '@/types/admin'

import AdminTaskProgress from '@/components/admin/AdminTaskProgress.vue'
import AdminProblemCards from '@/components/admin/AdminProblemCards.vue'
import AdminIntegrityCheck from '@/components/admin/AdminIntegrityCheck.vue'
import AdminDuplicates from '@/components/admin/AdminDuplicates.vue'
import AdminNearDuplicates from '@/components/admin/AdminNearDuplicates.vue'

const message = useMessage()
const router = useRouter()
const route = useRoute()

// ── 子页面（小菜单）状态：URL 持久化，刷新后停留在原小页面 ──
type GovernanceTab = 'cleanup' | 'integrity' | 'duplicates' | 'neardup'
const GOVERNANCE_TABS: GovernanceTab[] = ['cleanup', 'integrity', 'duplicates', 'neardup']

function initialTab(): GovernanceTab {
  const t = route.query.tab
  return t && GOVERNANCE_TABS.includes(t as GovernanceTab) ? (t as GovernanceTab) : 'cleanup'
}
const activeTab = ref<GovernanceTab>(initialTab())

watch(activeTab, (tab) => {
  const query = { ...route.query }
  if (tab === 'cleanup') {
    delete query.tab
  } else {
    query.tab = tab
  }
  router.replace({ query })
})

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

// ── 批量删除 / 去重 ──
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
      ? {
          groups_processed: r.groups_processed ?? 0,
          files_deleted: r.files_deleted ?? 0,
          freed_bytes: r.freed_bytes ?? 0,
        }
      : null
    if (!r || r.files_deleted === 0) {
      message.info('未找到可删除的重复文件')
    } else {
      message.success(
        `去重完成：处理 ${r.groups_processed ?? 0} 组，删除 ${r.files_deleted ?? 0} 个冗余文件，释放 ${formatSize(r.freed_bytes ?? 0)} 空间`,
      )
    }
    loadDuplicates()
  } else {
    const label =
      r?.label === 'untagged'
        ? '无标签素材'
        : r?.label === 'analysis_failed'
          ? '分析失败素材'
          : '素材'
    message.success(
      `已删除 ${r?.deleted_count ?? 0} 个${label}，释放 ${formatSize(r?.freed_bytes ?? 0)} 空间`,
    )
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
  } catch {
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
  } catch {
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
  } catch {
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
    const { data } = await apiClient.post<{ message: string; task_id: number }>(
      '/admin/deduplicate',
    )
    adminTask.value = {
      id: data.task_id,
      type: 'deduplicate',
      status: 'pending',
      progress: 0,
      total: 0,
      done: 0,
      result: null,
      error: null,
    }
    message.success(data.message)
    startAdminPolling(data.task_id, () => handleAdminTaskDone())
  } catch (e) {
    message.error(getApiErrorMessage(e, '去重删除失败'))
  } finally {
    deduplicating.value = false
  }
}

// ── 清理孤立文件 ──

async function cleanOrphans() {
  try {
    const res = await apiClient.post('/admin/cleanup-orphans')
    message.success(
      `已删除 ${res.data.deleted_count} 个孤立文件，释放 ${formatSize(res.data.freed_bytes)} 空间`,
    )
    await loadIntegrity()
    await loadAll() // 顶部统计（存储总大小/来源分布）同步刷新
  } catch {
    message.error('清理失败')
  }
}

// ── 批量删除（异步任务）──

/** 提交批量删除任务（按条件或按 ID 列表）并开启进度轮询 */
async function submitBatchDelete(payload: { ids?: string[]; condition?: string }) {
  const { data } = await apiClient.post<{ message: string; task_id: number }>(
    '/admin/batch-delete',
    payload,
  )
  adminTask.value = {
    id: data.task_id,
    type: 'batch_delete',
    status: 'pending',
    progress: 0,
    total: 0,
    done: 0,
    result: null,
    error: null,
  }
  message.success(data.message)
  startAdminPolling(data.task_id, () => handleAdminTaskDone())
}

/** 按条件批量删除（无标签 / 分析失败） */
async function batchDeleteByCondition(condition: string) {
  try {
    if (condition === 'untagged') clearingUntagged.value = true
    else clearingFailed.value = true
    await submitBatchDelete({ condition })
  } catch {
    message.error('批量删除失败')
  } finally {
    clearingUntagged.value = false
    clearingFailed.value = false
  }
}

/** 近似重复页：勾选素材执行物理删除（冗余文件直接释放空间，不走垃圾桶；复用批量删除任务 + 审计留痕） */
async function handleNearDuplicateDelete(ids: string[]) {
  try {
    await submitBatchDelete({ ids })
  } catch (e) {
    message.error(getApiErrorMessage(e, '删除失败'))
  }
}

/** 近似重复页「删除两张」：子组件已提交批量删除任务，接管进度轮询（任务完成后刷新统计） */
function handleTaskStarted(taskId: number, msg: string) {
  adminTask.value = {
    id: taskId,
    type: 'batch_delete',
    status: 'pending',
    progress: 0,
    total: 0,
    done: 0,
    result: null,
    error: null,
  }
  message.success(msg)
  startAdminPolling(taskId, () => handleAdminTaskDone())
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
  <div class="governance-page">
    <h2>数据治理</h2>
    <p class="subtitle">批量清理、完整性检查与去重维护</p>

    <!-- ====== 后台任务进度（全局） ====== -->
    <admin-task-progress :task="adminTask" />

    <!-- ====== 子页面小菜单 ====== -->
    <n-tabs v-model:value="activeTab" type="line" animated>
      <!-- 批量清理 -->
      <n-tab-pane name="cleanup" tab="批量清理">
        <admin-problem-cards
          :stats="stats"
          :clearing-untagged="clearingUntagged"
          :clearing-failed="clearingFailed"
          @delete-untagged="batchDeleteByCondition('untagged')"
          @delete-failed="batchDeleteByCondition('analysis_failed')"
        />
        <p style="color: #999; font-size: 12px">
          💡 提示：清理无标签或分析失败的素材可回收空间；删除前请确认这些素材确实不再需要。
        </p>
      </n-tab-pane>

      <!-- 数据完整性 -->
      <n-tab-pane name="integrity" tab="数据完整性">
        <admin-integrity-check
          :missing-files="missingFiles"
          :orphan-files="orphanFiles"
          :orphan-bytes="orphanSize"
          :checking="checking"
          @recheck="loadIntegrity"
          @clean-orphans="cleanOrphans"
        />
      </n-tab-pane>

      <!-- 重复文件 -->
      <n-tab-pane name="duplicates" tab="重复文件">
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
      </n-tab-pane>

      <!-- 近似重复（感知哈希） -->
      <n-tab-pane name="neardup" tab="近似重复">
        <admin-near-duplicates
          @delete-selected="handleNearDuplicateDelete"
          @task-started="handleTaskStarted"
        />
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.governance-page {
  max-width: 1100px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 24px;
}
</style>
