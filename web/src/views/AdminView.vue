<script setup lang="ts">
/** 高级素材管理页：统计仪表盘、存储分析、完整性检查、批量操作。 */

import { h, ref, computed, onMounted, onUnmounted } from 'vue'
import { NTag, NButton, NPopconfirm, NDataTable, NStatistic, useMessage } from 'naive-ui'
import apiClient from '@/api/client'

const message = useMessage()

// ── 类型定义 ──

interface MonthStat { month: string; count: number }
interface SourceStat { source_type: string; count: number }
interface MediaStat { media_type: string; count: number }
interface StatusStat { status: string; count: number; label: string }
interface LargeFile { id: string; file_path: string; source_type: string; created_at: string | null; size_bytes: number; exists: boolean }
interface MissingFile { file_path: string; inspiration_ids: string[] }
interface OrphanFile { file_path: string; size_bytes: number }
interface DuplicateGroup { hash: string; files: { id: string; file_path: string; size_bytes: number }[] }

interface Stats {
  total_count: number
  total_size_bytes: number
  thumbnail_size_bytes: number
  images_size_bytes: number
  untagged_count: number
  analysis_failed_count: number
  favorite_count: number
  total_tags: number
  tombstone_count: number
  by_source_type: SourceStat[]
  by_media_type: MediaStat[]
  by_analysis_status: StatusStat[]
  by_month: MonthStat[]
}

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
const dedupResult = ref<{ groups_processed: number; files_deleted: number; freed_bytes: number } | null>(null)

// 后台任务（批量删除/去重）—— 数据库驱动任务队列，轮询进度
interface AdminTask {
  id: number
  type: string
  status: string
  progress: number
  total: number
  done: number
  result: {
    label?: string
    deleted_count?: number
    freed_bytes?: number
    groups_processed?: number
    files_deleted?: number
  } | null
  error: string | null
}
const adminTask = ref<AdminTask | null>(null)
let adminPollTimer: ReturnType<typeof setTimeout> | null = null
let adminPollSeq = 0  // 轮询代际号：stop/重启时自增，使在途请求返回后不再续排

function stopAdminPolling() {
  adminPollSeq += 1  // 自增代际号，使当前轮询链失效，防止在途请求返回后重新调度
  if (adminPollTimer) { clearTimeout(adminPollTimer); adminPollTimer = null }
}

/** 轮询后台任务状态（约 1 秒一次），完成后执行 onDone 回调 */
function startAdminPolling(taskId: number, onDone: () => void) {
  stopAdminPolling()
  const seq = adminPollSeq  // 当前代际：stopAdminPolling 已自增，旧链的 seq 与之不符即失效
  let consecutiveFailures = 0  // 连续失败次数，失败时有限次重试而非直接停止
  const poll = async () => {
    if (seq !== adminPollSeq) return  // 已被 stop/新轮询取代，不再调度
    try {
      const { data } = await apiClient.get<AdminTask>(`/tasks/${taskId}`)
      if (seq !== adminPollSeq) return  // 在途请求返回前已被停止，丢弃结果
      consecutiveFailures = 0
      adminTask.value = data
      if (data.status === 'success' || data.status === 'failed' || data.status === 'cancelled') {
        stopAdminPolling()
        if (data.status === 'success') {
          onDone()
        } else if (data.status === 'failed') {
          message.error(`任务失败：${data.error || '未知错误'}`)
        } else {
          message.info('任务已取消')
        }
        return
      }
      adminPollTimer = setTimeout(poll, 1000)
    } catch {
      if (seq !== adminPollSeq) return
      consecutiveFailures += 1
      if (consecutiveFailures >= 5) {
        // 连续多次失败才停止，避免后端重启/网络抖动导致任务进度卡死
        stopAdminPolling()
        message.error('获取任务状态多次失败，已停止轮询，请稍后手动刷新')
        return
      }
      // 有限次重试：间隔放大到 3 秒，继续续排轮询链
      adminPollTimer = setTimeout(poll, 3000)
    }
  }
  poll()
}

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

/** 恢复进行中的后台任务：刷新页面后查询是否有 pending/running 的删除/去重任务并继续轮询 */
async function resumeAdminTask() {
  try {
    const { data } = await apiClient.get<{ items: AdminTask[] }>('/tasks', { params: { size: 20 } })
    const active = data.items.find((t) =>
      (t.type === 'batch_delete' || t.type === 'deduplicate') &&
      (t.status === 'pending' || t.status === 'running')
    )
    if (active) {
      adminTask.value = active
      startAdminPolling(active.id, () => handleAdminTaskDone())
    }
  } catch { /* 静默 */ }
}

// ── 计算属性 ──

const totalBytes = computed(() => stats.value?.total_size_bytes ?? 0)
const imagesBytes = computed(() => stats.value?.images_size_bytes ?? 0)
const thumbnailsBytes = computed(() => stats.value?.thumbnail_size_bytes ?? 0)
const orphanBytes = computed(() => orphanSize.value)
const dupBytes = computed(() => dupSize.value)

// ── 自适应大小格式化（值保持在 1-1000 范围 + 单位）──

interface SizeDisplay { value: string; unit: string }

function smartSize(bytes: number): SizeDisplay {
  if (bytes < 1024) return { value: String(bytes), unit: 'B' }
  if (bytes < 1024 * 1024) return { value: (bytes / 1024).toFixed(1), unit: 'KB' }
  if (bytes < 1024 * 1024 * 1024) return { value: (bytes / (1024 * 1024)).toFixed(1), unit: 'MB' }
  return { value: (bytes / (1024 * 1024 * 1024)).toFixed(2), unit: 'GB' }
}

/** 返回 "数值 单位" 的完整字符串，如 "462.9 MB" */
function fmtSize(bytes: number): string {
  const s = smartSize(bytes)
  return s.value + ' ' + s.unit
}

// ── 来源类型中文映射 ──

function sourceLabel(t: string): string {
  const labels: Record<string, string> = {
    xiaohongshu: '小红书',
    douyin: '抖音',
    scraper: '自动采集',
    manual_upload: '手动上传',
    browser_extension: '浏览器插件',
  }
  return labels[t] || t
}

function statusLabel(s: string): string {
  const labels: Record<string, string> = {
    done: '已分析', error: '分析失败', pending: '未分析', none: '未分析',
  }
  return labels[s] || s
}

// ── 格式化文件大小 ──

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB'
  return bytes + ' B'
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

// ── 最大文件表格列 ──

const fileColumns = [
  { title: '文件路径', key: 'file_path', width: 320, ellipsis: { tooltip: true } },
  {
    title: '来源', key: 'source_type', width: 80,
    render: (row: LargeFile) => sourceLabel(row.source_type),
  },
  {
    title: '大小', key: 'size_bytes', width: 90,
    render: (row: LargeFile) => formatSize(row.size_bytes),
  },
  {
    title: '状态', key: 'exists', width: 70,
    render: (row: LargeFile) =>
      row.exists
        ? h(NTag, { type: 'success', size: 'tiny' }, '正常')
        : h(NTag, { type: 'error', size: 'tiny' }, '缺失'),
  },
]

// ── 孤儿文件表格列 ──

const orphanColumns = [
  { title: '文件路径', key: 'file_path', ellipsis: { tooltip: true } },
  { title: '大小', key: 'size_bytes', width: 100, render: (row: OrphanFile) => formatSize(row.size_bytes) },
]

// ── 重复文件表格列 ──

const dupColumns = [
  { title: '文件路径', key: 'file_path', ellipsis: { tooltip: true } },
  {
    title: '大小', key: 'size_bytes', width: 100,
    render: (row: { file_path: string; size_bytes: number }) => formatSize(row.size_bytes),
  },
]

// ── 辅助函数 ──

/** 来源类型颜色 */
function sourceColor(t: string): string {
  const colors: Record<string, string> = {
    xiaohongshu: '#ff2442',
    douyin: '#111',
    scraper: '#18a058',
    manual_upload: '#2080f0',
    browser_extension: '#f0a020',
  }
  return colors[t] || '#999'
}

/** 分析状态颜色 */
function statusColor(s: string): string {
  const colors: Record<string, string> = {
    done: '#18a058',
    error: '#d03050',
    pending: '#999',
    none: '#999',
  }
  return colors[s] || '#999'
}

// ── 生命周期 ──

onMounted(() => {
  loadAll()
  resumeAdminTask()
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
    <div class="stat-cards">
      <n-card size="small">
        <n-statistic label="素材总数" :value="stats?.total_count ?? '-'" />
      </n-card>
      <n-card size="small">
        <n-statistic label="存储总大小" :value="fmtSize(totalBytes)" />
      </n-card>
      <n-card size="small">
        <n-statistic label="图片占用" :value="fmtSize(imagesBytes)" />
      </n-card>
      <n-card size="small">
        <n-statistic label="缩略图占用" :value="fmtSize(thumbnailsBytes)" />
      </n-card>
      <n-card size="small">
        <n-statistic label="标签总数" :value="stats?.total_tags ?? '-'" />
      </n-card>
      <n-card size="small">
        <n-statistic label="收藏数" :value="stats?.favorite_count ?? '-'" />
      </n-card>
      <n-card size="small" :bordered="true" style="border-color: #2080f0">
        <n-statistic label="📋 墓碑表记录" :value="stats?.tombstone_count ?? '-'" />
        <template #footer>
          <span style="font-size: 11px; color: #999">已采集 URL，防止重复入库</span>
        </template>
      </n-card>
    </div>

    <!-- ====== 后台任务进度 ====== -->
    <n-alert v-if="adminTask && (adminTask.status === 'pending' || adminTask.status === 'running')" type="info" style="margin: 16px 0">
      <template #header>
        {{ adminTask.type === 'deduplicate' ? '去重任务' : '批量删除任务' }} #{{ adminTask.id }} 进行中
        <span v-if="adminTask.total > 0">（{{ adminTask.done }}/{{ adminTask.total }}）</span>
      </template>
      <n-progress type="line" :percentage="adminTask.progress" style="margin-top:8px" />
    </n-alert>

    <!-- ====== 问题概览卡片 ====== -->
    <div class="stat-cards" style="margin-top: 16px">
      <n-card size="small" :bordered="true" style="border-color: #f0a020">
        <n-statistic label="⚠️ 无标签素材" :value="stats?.untagged_count ?? '-'" />
        <template #footer>
          <n-popconfirm @positive-click="batchDeleteByCondition('untagged')">
            <template #trigger>
              <n-button size="tiny" type="warning" ghost :loading="clearingUntagged"
                :disabled="!stats?.untagged_count">
                批量删除
              </n-button>
            </template>
            确定删除所有无标签素材？此操作不可撤销。
          </n-popconfirm>
        </template>
      </n-card>
      <n-card size="small" :bordered="true" style="border-color: #d03050">
        <n-statistic label="❌ 分析失败素材" :value="stats?.analysis_failed_count ?? '-'" />
        <template #footer>
          <n-popconfirm @positive-click="batchDeleteByCondition('analysis_failed')">
            <template #trigger>
              <n-button size="tiny" type="error" ghost :loading="clearingFailed"
                :disabled="!stats?.analysis_failed_count">
                批量删除
              </n-button>
            </template>
            确定删除所有分析失败的素材？此操作不可撤销。
          </n-popconfirm>
        </template>
      </n-card>
    </div>

    <!-- ====== 分布统计 ====== -->
    <div class="dist-row">
      <!-- 按来源 -->
      <n-card title="素材来源分布" size="small" style="flex: 1">
        <div v-if="stats?.by_source_type?.length">
          <div v-for="s in stats.by_source_type" :key="s.source_type" class="dist-item">
            <span>{{ sourceLabel(s.source_type) }}</span>
            <span class="dist-bar-wrap">
              <span class="dist-bar"
                :style="{ width: Math.max(s.count / stats.total_count * 100, 2) + '%', background: sourceColor(s.source_type) }">
              </span>
            </span>
            <span>{{ s.count }}</span>
          </div>
        </div>
        <n-empty v-else description="暂无数据" size="small" />
      </n-card>

      <!-- 按月 -->
      <n-card title="月度新增趋势" size="small" style="flex: 1">
        <div v-if="stats?.by_month?.length">
          <div v-for="m in stats.by_month" :key="m.month" class="dist-item">
            <span>{{ m.month }}</span>
            <span class="dist-bar-wrap">
              <span class="dist-bar" style="background: #2080f0"
                :style="{ width: Math.max(m.count / Math.max(...stats.by_month.map(x => x.count)) * 100, 2) + '%' }">
              </span>
            </span>
            <span>{{ m.count }}</span>
          </div>
        </div>
        <n-empty v-else description="暂无数据" size="small" />
      </n-card>
    </div>

    <div class="dist-row">
      <!-- 按分析状态 -->
      <n-card title="分析状态分布" size="small" style="flex: 1">
        <div v-if="stats?.by_analysis_status?.length">
          <div v-for="s in stats.by_analysis_status" :key="s.status" class="dist-item">
            <span>{{ s.label }}</span>
            <span class="dist-bar-wrap">
              <span class="dist-bar"
                :style="{ width: Math.max(s.count / stats.total_count * 100, 2) + '%', background: statusColor(s.status) }">
              </span>
            </span>
            <span>{{ s.count }}</span>
          </div>
        </div>
        <n-empty v-else description="暂无数据" size="small" />
      </n-card>

      <!-- 按媒体类型 -->
      <n-card title="媒体类型分布" size="small" style="flex: 1">
        <div v-if="stats?.by_media_type?.length">
          <div v-for="m in stats.by_media_type" :key="m.media_type" class="dist-item">
            <span>{{ m.media_type === 'image' ? '🖼 图片' : m.media_type === 'video' ? '🎬 视频' : m.media_type }}</span>
            <span class="dist-bar-wrap">
              <span class="dist-bar" style="background: #18a058"
                :style="{ width: Math.max(m.count / stats.total_count * 100, 2) + '%' }">
              </span>
            </span>
            <span>{{ m.count }}</span>
          </div>
        </div>
        <n-empty v-else description="暂无数据" size="small" />
      </n-card>
    </div>

    <!-- ====== 最大文件 ====== -->
    <n-card title="占用空间最大的文件 (Top 20)" size="small" style="margin-bottom: 24px">
      <n-data-table
        :columns="fileColumns"
        :data="largestFiles"
        :bordered="false"
        size="small"
        :max-height="400"
      />
    </n-card>

    <!-- ====== 完整性检查 ====== -->
    <n-card title="数据完整性检查" size="small" style="margin-bottom: 24px">
      <template #header-extra>
        <n-space>
          <n-button size="small" :loading="checking" @click="loadIntegrity">
            重新检查
          </n-button>
          <n-popconfirm @positive-click="cleanOrphans" v-if="orphanFiles.length > 0">
            <template #trigger>
              <n-button size="small" type="error" ghost>
                清理孤立文件 ({{ orphanFiles.length }})
              </n-button>
            </template>
            确定删除所有 {{ orphanFiles.length }} 个孤立文件？释放约 {{ fmtSize(orphanBytes) }}。此操作不可撤销。
          </n-popconfirm>
        </n-space>
      </template>

      <!-- 缺失文件 -->
      <div v-if="missingFiles.length > 0" style="margin-bottom: 16px">
        <h4 style="color: #d03050; margin: 0 0 8px">
          ❌ 缺失文件 ({{ missingFiles.length }}) — 数据库有记录但文件不存在
        </h4>
        <n-data-table
          :columns="[
            { title: '预期文件路径', key: 'file_path', ellipsis: { tooltip: true } },
            { title: '关联素材数', key: 'inspiration_ids', width: 100, render: (row: MissingFile) => row.inspiration_ids.length },
          ]"
          :data="missingFiles.slice(0, 50)"
          :bordered="false"
          size="small"
          :max-height="300"
        />
      </div>

      <!-- 孤立文件 -->
      <div v-if="orphanFiles.length > 0">
        <h4 style="color: #f0a020; margin: 0 0 8px">
          ⚠️ 孤立文件 ({{ orphanFiles.length }}) — 磁盘有文件但数据库无记录 · 共 {{ fmtSize(orphanBytes) }}
        </h4>
        <n-data-table
          :columns="orphanColumns"
          :data="orphanFiles.slice(0, 50)"
          :bordered="false"
          size="small"
          :max-height="300"
        />
      </div>

      <n-empty
        v-if="missingFiles.length === 0 && orphanFiles.length === 0 && !checking"
        description="✅ 数据完整，未发现缺失或孤立文件"
        size="small"
      />
    </n-card>

    <!-- ====== 重复检测 ====== -->
    <n-card title="重复文件检测" size="small" style="margin-bottom: 24px">
      <template #header-extra>
        <n-space>
          <n-button size="small" :loading="checking" @click="scanDuplicates">
            检测重复
          </n-button>
          <n-popconfirm
            v-if="duplicates.length > 0"
            @positive-click="deduplicate"
          >
            <template #trigger>
              <n-button
                size="small"
                type="error"
                ghost
                :loading="deduplicating"
                :disabled="duplicates.length === 0"
              >
                删除重复文件 ({{ dupCount }})
              </n-button>
            </template>
            确定删除所有 {{ dupCount }} 个重复文件？<br/>
            每组将保留评分最高的 1 个（优先有标签/收藏/AI已分析的素材）。<br/>
            将释放约 {{ fmtSize(dupBytes) }} 空间。<br/>
            <b style="color: #d03050">此操作物理删除文件，不可撤销！</b>
          </n-popconfirm>
        </n-space>
      </template>

      <!-- 去重结果 -->
      <n-alert
        v-if="dedupResult && dedupResult.files_deleted > 0"
        type="success"
        style="margin-bottom: 12px"
      >
        已处理 {{ dedupResult.groups_processed }} 组，删除 {{ dedupResult.files_deleted }} 个文件，
        释放 {{ fmtSize(dedupResult.freed_bytes) }} 空间
      </n-alert>
      <n-alert
        v-if="dedupResult && dedupResult.files_deleted === 0"
        type="info"
        style="margin-bottom: 12px"
      >
        未发现可清理的重复文件
      </n-alert>

      <div v-if="duplicates.length > 0">
        <p style="color: #f0a020; margin-bottom: 12px">
          ⚠️ 发现 {{ duplicates.length }} 组重复文件，共 {{ dupCount }} 个冗余副本，浪费 {{ fmtSize(dupBytes) }} 空间
        </p>
        <div v-for="(group, gi) in duplicates.slice(0, 20)" :key="group.hash" style="margin-bottom: 16px">
          <n-tag type="info" size="tiny" style="margin-bottom: 6px">
            {{ group.files.length }} 个相同文件 ({{ formatSize(group.files[0].size_bytes) }} × {{ group.files.length }})
          </n-tag>
          <n-data-table
            :columns="dupColumns"
            :data="group.files"
            :bordered="false"
            size="small"
          />
        </div>
      </div>

      <n-empty v-else-if="!checking" description="✅ 未发现完全重复的文件" size="small" />
    </n-card>

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

/* 统计卡片网格 */
.stat-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

/* 分布统计行 */
.dist-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

/* 分布条 */
.dist-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}
.dist-item span:first-child {
  width: 70px;
  text-align: right;
  flex-shrink: 0;
  color: #666;
}
.dist-item span:last-child {
  width: 36px;
  text-align: right;
  flex-shrink: 0;
  font-weight: 600;
}
.dist-bar-wrap {
  flex: 1;
  height: 14px;
  background: #f0f0f0;
  border-radius: 7px;
  overflow: hidden;
}
.dist-bar {
  display: block;
  height: 100%;
  border-radius: 7px;
  transition: width 0.4s ease;
  min-width: 4px;
}
</style>
