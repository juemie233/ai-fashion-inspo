<script setup lang="ts">
/** 采集管理页：创建/查看采集任务，管理采集源。Phase 4 完整功能。 */

import { h, ref, computed, onMounted } from 'vue'
import { NTag, NButton, NCheckbox, NSpin, NPopconfirm, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()

/** 复制文本到剪贴板（含降级方案） */
function copyText(text: string) {
  try {
    navigator.clipboard.writeText(text).then(
      () => message.success('已复制到剪贴板'),
      () => { throw new Error('clipboard denied') }
    )
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;left:-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    message.success('已复制到剪贴板')
  }
}

interface ScraperTask {
  id: number
  platform: string
  status: string
  config: string | null
  items_found: number
  items_added: number
  error?: string | null
  created_at: string
}

interface ScraperSource {
  platform: string
  name: string
  status: string
  features: string[]
  note: string
}

const sources = ref<ScraperSource[]>([])
const tasks = ref<ScraperTask[]>([])

/** 新建采集表单 */
const formPlatform = ref('xiaohongshu')
const formKeywords = ref('')
const formMaxCount = ref(100)   // 兜底值，实际从后端配置读取
const formHeadless = ref(false)
const formCdp = ref(true)        // 默认 CDP 模式
const formCdpPort = ref(9222)

onMounted(async () => {
  try {
    const [sRes, tRes] = await Promise.all([
      apiClient.get('/scraper/sources'),
      apiClient.get('/scraper/tasks'),
    ])
    sources.value = sRes.data.sources
    tasks.value = tRes.data
    // 从后端读取可配置的默认采集数量
    if (sRes.data.default_max_count) {
      formMaxCount.value = sRes.data.default_max_count
    }
  } catch (e: any) {
    message.error('加载采集数据失败')
  }
})

/** 刷新任务列表 */
async function refreshTasks() {
  try {
    const tRes = await apiClient.get('/scraper/tasks')
    tasks.value = tRes.data
  } catch (e: any) {
    // 静默失败
  }
}

// ── 采集结果预览 ──

interface ResultItem {
  id: string
  file_path: string
  thumbnail_path: string | null
  source_url: string
  is_favorite: boolean
  created_at: string | null
}

const resultsTaskId = ref<number | null>(null)  // 当前正在查看结果的任务 ID
const resultsItems = ref<ResultItem[]>([])
const resultsTotal = ref(0)
const resultsLoading = ref(false)
const selectedIds = ref<Set<string>>(new Set())
const deletingResults = ref(false)

/** 查看某任务的采集结果 */
async function viewResults(taskId: number) {
  // 如果已经展开，关闭
  if (resultsTaskId.value === taskId) {
    resultsTaskId.value = null
    resultsItems.value = []
    selectedIds.value = new Set()
    return
  }
  resultsTaskId.value = taskId
  resultsLoading.value = true
  selectedIds.value = new Set()
  try {
    const res = await apiClient.get(`/scraper/tasks/${taskId}/results`, { params: { size: 200 } })
    resultsItems.value = res.data.items
    resultsTotal.value = res.data.total
  } catch (e: any) {
    message.error('加载采集结果失败')
    resultsTaskId.value = null
  } finally {
    resultsLoading.value = false
  }
}

/** 切换选中 */
function toggleSelect(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function isSelected(id: string): boolean {
  return selectedIds.value.has(id)
}

/** 全选/取消 */
function selectAll() {
  if (selectedIds.value.size === resultsItems.value.length) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(resultsItems.value.map(i => i.id))
  }
}

/** 删除选中 */
async function deleteSelected() {
  if (selectedIds.value.size === 0) return
  deletingResults.value = true
  try {
    const res = await apiClient.post(`/scraper/tasks/${resultsTaskId.value}/results/batch-delete`, {
      ids: [...selectedIds.value],
    })
    message.success(`已删除 ${res.data.deleted_count} 个素材`)
    // 从本地列表中移除已删除的
    resultsItems.value = resultsItems.value.filter(i => !selectedIds.value.has(i.id))
    selectedIds.value = new Set()
    resultsTotal.value = res.data.remaining
    // 如果全删了，关闭视图
    if (res.data.remaining === 0) {
      resultsTaskId.value = null
      resultsItems.value = []
    }
    // 刷新任务列表更新计数
    await refreshTasks()
  } catch (e: any) {
    message.error('删除失败')
  } finally {
    deletingResults.value = false
  }
}

/** 删除单条任务（逻辑删除） */
const deletingTask = ref<number | null>(null)
async function deleteSingleTask(taskId: number) {
  try {
    deletingTask.value = taskId
    await apiClient.delete(`/scraper/tasks/${taskId}`)
    tasks.value = tasks.value.filter(t => t.id !== taskId)
    message.success('已删除')
  } catch (e: any) {
    message.error('删除失败')
  } finally {
    deletingTask.value = null
  }
}

/** 清空所有采集任务历史 */
const clearing = ref(false)
async function clearAllTasks() {
  try {
    clearing.value = true
    const res = await apiClient.delete('/scraper/tasks')
    tasks.value = []
    message.success(`已清空 ${res.data.deleted} 条采集任务记录`)
  } catch (e: any) {
    message.error('清空失败')
  } finally {
    clearing.value = false
  }
}

/** 重试所有失败任务 */
const retrying = ref(false)
async function retryFailedTasks() {
  try {
    retrying.value = true
    const res = await apiClient.post('/scraper/tasks/retry-failed')
    message.success(res.data.message)
    await refreshTasks()
  } catch (e: any) {
    if (e.response?.status === 404) {
      message.info('没有失败的采集任务')
    } else {
      message.error(e.response?.data?.detail || '重试失败')
    }
  } finally {
    retrying.value = false
  }
}

async function createTask() {
  try {
    await apiClient.post('/scraper/tasks', {
      platform: formPlatform.value,
      keywords: formKeywords.value
        .split(',')
        .map((k) => k.trim())
        .filter(Boolean),
      max_count: formMaxCount.value,
      headless: formHeadless.value,
      cdp_port: formCdp.value ? formCdpPort.value : null,
    })
    await refreshTasks()
    message.success('采集任务已创建')
  } catch (e: any) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'object' && detail !== null) {
      // 结构化错误（如 CDP 检测失败）
      message.error(detail.error || '创建任务失败')
      if (detail.command) {
        // 把启动命令复制到剪贴板，方便用户执行
        setTimeout(() => {
          copyText(detail.command)
        }, 500)
      }
    } else {
      message.error(detail || '创建任务失败')
    }
  }
}

/** 测试 CDP 连接 */
const cdpChecking = ref(false)
const cdpStatus = ref<'idle' | 'ok' | 'fail'>('idle')
const cdpDetail = ref('')

async function testCdp() {
  cdpChecking.value = true
  cdpStatus.value = 'idle'
  try {
    const res = await apiClient.get(`/scraper/cdp-check/${formCdpPort.value}`)
    if (res.data.available && res.data.is_google_chrome) {
      cdpStatus.value = 'ok'
      cdpDetail.value = res.data.detail
      message.success(cdpDetail.value)
    } else if (res.data.available && !res.data.is_google_chrome) {
      cdpStatus.value = 'fail'
      cdpDetail.value = res.data.detail
      message.error('检测到非 Google Chrome 浏览器！请关闭后重新启动 Google Chrome 调试模式。')
    } else {
      cdpStatus.value = 'fail'
      cdpDetail.value = res.data.detail
      message.warning(cdpDetail.value + '。请先启动调试 Chrome。')
    }
  } catch (e: any) {
    cdpStatus.value = 'fail'
    cdpDetail.value = '检测请求失败'
  } finally {
    cdpChecking.value = false
  }
}

/** 平台显示名称映射（兜底：sources 未加载或匹配不到时使用） */
const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  browser_extension: '浏览器插件',
  scraper: '自动采集',
  manual_upload: '手动上传',
}

function platformName(p: string): string {
  return sources.value.find((s) => s.platform === p)?.name || PLATFORM_LABELS[p] || p
}

function statusLabel(s: string): string {
  const labels: Record<string, string> = {
    pending: '等待中', running: '运行中', completed: '已完成',
    failed: '失败', cancelled: '已取消',
  }
  return labels[s] || s
}

function statusType(s: string): 'default' | 'info' | 'success' | 'error' | 'warning' {
  const types: Record<string, 'default' | 'info' | 'success' | 'error' | 'warning'> = {
    pending: 'default', running: 'info', completed: 'success',
    failed: 'error', cancelled: 'warning',
  }
  return types[s] || 'default'
}

/** 安全格式化日期字符串 */
function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return '-'
    return d.toLocaleString('zh-CN')
  } catch {
    return '-'
  }
}

/** 解析任务配置中的关键词 */
function parseKeywords(config: string | null): string {
  if (!config) return '-'
  try {
    const cfg = JSON.parse(config)
    return (cfg.keywords || []).join(', ') || '-'
  } catch {
    return '-'
  }
}

// ==================== 选项4: 成功率统计 ====================
const taskStats = computed(() => {
  const total = tasks.value.length
  const completed = tasks.value.filter(t => t.status === 'completed').length
  const failed = tasks.value.filter(t => t.status === 'failed').length
  const pending = tasks.value.filter(t => t.status === 'pending').length
  const running = tasks.value.filter(t => t.status === 'running').length
  const rate = total > 0 ? Math.round((completed / total) * 100) : 0
  return { total, completed, failed, pending, running, rate }
})

const hasFailedTasks = computed(() => tasks.value.some(t => t.status === 'failed'))

// ==================== 表格列定义 ====================
const tableColumns = computed(() => [
  {
    title: '平台',
    key: 'platform',
    width: 80,
    render: (row: ScraperTask) => platformName(row.platform),
  },
  {
    title: '关键词',
    key: 'config',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row: ScraperTask) => parseKeywords(row.config),
  },
  {
    title: '状态',
    key: 'status',
    width: 80,
    render: (row: ScraperTask) => h(
      NTag,
      { type: statusType(row.status), size: 'small' },
      statusLabel(row.status),
    ),
  },
  {
    title: '发现',
    key: 'items_found',
    width: 60,
  },
  {
    title: '新增',
    key: 'items_added',
    width: 60,
  },
  {
    title: '错误原因',
    key: 'error',
    width: 160,
    ellipsis: { tooltip: true },
    render: (row: ScraperTask) => {
      if (!row.error) return '-'
      return h(
        'span',
        {
          style: {
            color: '#d03050',
            cursor: 'pointer',
            textDecoration: 'underline',
            textUnderlineOffset: '2px',
          },
          title: row.error,
          onClick: () => copyText(row.error!),
        },
        row.error.length > 30 ? row.error.slice(0, 30) + '…' : row.error,
      )
    },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 160,
    render: (row: ScraperTask) => formatDate(row.created_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (row: ScraperTask) => {
      const btns = []
      if (row.items_added > 0) {
        btns.push(
          h(NButton, {
            size: 'tiny',
            type: resultsTaskId.value === row.id ? 'warning' : 'primary',
            ghost: true,
            onClick: () => viewResults(row.id),
          }, resultsTaskId.value === row.id ? '收起' : '查看结果'),
        )
      }
      btns.push(
        h(
          NPopconfirm,
          { onPositiveClick: () => deleteSingleTask(row.id) },
          {
            trigger: () =>
              h(NButton, {
                size: 'tiny',
                type: 'error',
                ghost: true,
                loading: deletingTask.value === row.id,
              }, '删除'),
            default: () => '确定删除此记录？（仅逻辑删除，不删除素材）',
          },
        ),
      )
      return h('span', { style: { display: 'flex', gap: '4px' } }, btns)
    },
  },
])

// ==================== 展开行：显示完整错误信息 ====================
function expandedRowRender(row: ScraperTask) {
  if (!row.error && !row.config) return null
  return h('div', { style: { padding: '12px 24px', maxWidth: '700px' } }, [
    row.config ? h('div', { style: { marginBottom: '8px' } }, [
      h('span', { style: { color: '#999', fontSize: '12px' } }, '任务配置：'),
      h('pre', { style: { margin: '4px 0', fontSize: '12px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' } },
        JSON.stringify(JSON.parse(row.config), null, 2)),
    ]) : null,
    row.error ? h('div', [
      h('span', { style: { color: '#d03050', fontSize: '12px' } }, '错误详情：'),
      h('pre', {
        style: {
          margin: '4px 0', fontSize: '12px', color: '#d03050',
          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          background: '#fef0f0', padding: '8px', borderRadius: '4px',
        },
      }, row.error),
    ]) : null,
  ])
}

// ==================== 平台状态提示映射 ====================
const platformHints = computed(() => {
  return sources.value.map(src => {
    let level: 'warning' | 'info' | 'error' = 'info'
    let tips: string[] = []
    switch (src.platform) {
      case 'xiaohongshu':
        level = 'warning'
        tips = [
          '需要有效的登录 Cookie 才能获取完整数据',
          '反爬检测严格，推荐手动导出 Cookie 后导入',
          '搜索功能依赖页面 DOM 结构，可能随平台更新失效',
          '如果搜索结果为空，请检查 Cookie 是否过期',
        ]
        break
      case 'douyin':
        level = 'error'
        tips = [
          '抖音网页版功能严重受限，仅能搜索公开内容',
          '搜索结果可能为空或不完整',
          '完整采集需配合移动端自动化方案',
          '不推荐作为主要采集渠道',
        ]
        break
      case 'browser_extension':
        level = 'info'
        tips = [
          '浏览器插件是目前最可靠的采集方式',
          '支持一键抓取当前页面内容，无需额外配置',
          '安装 Chrome 扩展后即可使用',
        ]
        break
    }
    return { ...src, level, tips }
  })
})

// ==================== 空状态引导标题 ====================
const emptyGuideTitle = computed(() => {
  return '暂无采集任务记录'
})

// 轮询：有运行中或等待中的任务时自动刷新
let pollTimer: ReturnType<typeof setInterval> | null = null
const hasActiveTasks = computed(() =>
  tasks.value.some(t => t.status === 'pending' || t.status === 'running'),
)

onMounted(() => {
  startPollIfNeeded()
})

function startPollIfNeeded() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    if (hasActiveTasks.value) {
      await refreshTasks()
      if (!hasActiveTasks.value) stopPoll()
    }
  }, 5000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
</script>

<template>
  <div class="scraper-page">
    <h2>采集管理</h2>
    <p class="subtitle">自动化采集小红书和抖音的穿搭内容。需要安装 Playwright。</p>

    <!-- 可用采集源 -->
    <n-card title="可用采集源" style="margin-bottom: 24px">
      <n-list>
        <n-list-item v-for="src in sources" :key="src.platform">
          <template #prefix>
            <n-tag :type="src.status === 'available' ? 'success' : 'warning'" size="small">
              {{ src.status === 'available' ? '可用' : '有限' }}
            </n-tag>
          </template>
          <n-thing :title="src.name" :description="src.note">
            <template #header-extra>
              <n-space>
                <n-tag v-for="f in src.features" :key="f" size="tiny" :bordered="false">
                  {{ f }}
                </n-tag>
              </n-space>
            </template>
          </n-thing>
        </n-list-item>
      </n-list>
    </n-card>

    <!-- 新建采集任务 -->
    <n-card title="新建采集任务" style="margin-bottom: 24px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="平台">
          <n-select
            v-model:value="formPlatform"
            :options="[
              { label: '小红书', value: 'xiaohongshu' },
              { label: '抖音 (有限支持)', value: 'douyin' },
            ]"
          />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input
            v-model:value="formKeywords"
            placeholder="多个关键词用逗号分隔，如: JK制服, 春季穿搭"
          />
        </n-form-item>
        <n-form-item label="数量上限">
          <n-input-number v-model:value="formMaxCount" :min="1" :max="500" />
        </n-form-item>
        <n-form-item label="CDP 模式">
          <n-switch v-model:value="formCdp" @update:value="() => { cdpStatus = 'idle' }" />
          <span style="margin-left: 8px; font-size: 12px; color: #18a058;">
            {{ formCdp ? '连接真实 Chrome（零检测，需先启动调试 Chrome）' : 'Playwright 启动浏览器' }}
          </span>
        </n-form-item>
        <n-form-item v-if="formCdp" label="CDP 端口">
          <n-space align="center">
            <n-input-number v-model:value="formCdpPort" :min="9222" :max="9230" style="width: 120px" />
            <n-button
              size="small"
              :loading="cdpChecking"
              :type="cdpStatus === 'ok' ? 'success' : cdpStatus === 'fail' ? 'warning' : 'default'"
              @click="testCdp"
            >
              {{ cdpChecking ? '检测中...' : cdpStatus === 'ok' ? '✓ 已连接' : cdpStatus === 'fail' ? '✗ 未连接' : '测试连接' }}
            </n-button>
          </n-space>
        </n-form-item>
        <n-form-item v-if="formCdp">
          <n-alert type="info" style="width: 100%">
            <template #header>
              💡 如何启动调试 Chrome？
            </template>
            <p style="margin: 4px 0; font-size: 12px; line-height: 1.8">
              请先关闭所有 Chrome 窗口，然后在<b>命令行</b>中执行以下命令启动调试模式：<br/>
              <code style="display: block; background: #f0f0f0; padding: 6px 10px; margin: 6px 0; border-radius: 4px; font-size: 11px; word-break: break-all; cursor: pointer; user-select: all;">
                "C:/Users/Administrator/AppData/Local/Google/Chrome/Application/chrome.exe" --remote-debugging-port={{ formCdpPort }} --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
              </code>
              启动后在 Chrome 中登录小红书，回来点击「<b>测试连接</b>」确认就绪，即可开始采集。
            </p>
          </n-alert>
        </n-form-item>
        <n-button type="primary" @click="createTask">
          开始采集
        </n-button>
      </n-form>
      <p style="color: #999; font-size: 12px; margin-top: 12px">
        ⚠️ 自动采集依赖于平台网页版，可靠性有限。推荐使用浏览器插件作为主要采集方式。
      </p>
    </n-card>

    <!-- 选项3: 采集可用性提醒卡片 -->
    <div class="platform-hints" style="margin-bottom: 24px">
      <n-alert
        v-for="hint in platformHints"
        :key="hint.platform"
        :type="hint.level"
        :title="hint.name + ' — ' + (hint.level === 'error' ? '⚠️ 可靠性低' : hint.level === 'warning' ? '⚡ 需要配置' : '✅ 推荐使用')"
        style="margin-bottom: 12px"
      >
        <ul style="margin: 4px 0; padding-left: 18px; font-size: 13px;">
          <li v-for="tip in hint.tips" :key="tip">{{ tip }}</li>
        </ul>
      </n-alert>
    </div>

    <!-- 任务历史 -->
    <n-card title="采集任务历史">
      <template #header-extra>
        <n-space align="center">
          <!-- 选项4: 成功率统计 -->
          <span
            v-if="taskStats.total > 0"
            style="font-size: 12px; color: #666; margin-right: 8px"
          >
            共 <b>{{ taskStats.total }}</b> 条 ·
            成功 <b style="color: #18a058">{{ taskStats.completed }}</b> ·
            失败 <b style="color: #d03050">{{ taskStats.failed }}</b> ·
            成功率 <b>{{ taskStats.rate }}%</b>
            <template v-if="taskStats.pending > 0 || taskStats.running > 0">
              · 进行中 <b style="color: #2080f0">{{ taskStats.pending + taskStats.running }}</b>
            </template>
          </span>
          <!-- 选项5: 重试失败按钮 -->
          <n-button
            v-if="hasFailedTasks"
            size="small"
            type="warning"
            ghost
            :loading="retrying"
            @click="retryFailedTasks"
          >
            重试所有失败任务
          </n-button>
          <n-popconfirm @positive-click="clearAllTasks">
            <template #trigger>
              <n-button size="small" :loading="clearing" type="error" ghost>
                清空历史
              </n-button>
            </template>
            确定清空所有采集任务记录？此操作不可撤销。
          </n-popconfirm>
        </n-space>
      </template>

      <!-- 有数据时显示表格 -->
      <template v-if="tasks.length > 0">
        <n-data-table
          :columns="tableColumns"
          :data="tasks"
          :bordered="false"
          :expanded-row-render="expandedRowRender"
          :row-key="(row: ScraperTask) => row.id"
          size="small"
        />
        <p style="color: #999; font-size: 12px; margin-top: 8px">
          💡 点击有错误原因的行可展开查看完整错误详情和任务配置。
        </p>
      </template>

      <!-- ====== 采集结果预览 ====== -->
      <div v-if="resultsTaskId !== null" class="results-panel">
        <n-spin :show="resultsLoading">
          <div class="results-header">
            <span>
              📋 本次采集结果（共 {{ resultsTotal }} 张）
            </span>
            <n-space>
              <n-button size="tiny" @click="selectAll">
                {{ selectedIds.size === resultsItems.length ? '取消全选' : '全选' }}
              </n-button>
              <n-popconfirm
                v-if="selectedIds.size > 0"
                @positive-click="deleteSelected"
              >
                <template #trigger>
                  <n-button size="tiny" type="error" ghost :loading="deletingResults">
                    删除选中 ({{ selectedIds.size }})
                  </n-button>
                </template>
                确定删除选中的 {{ selectedIds.size }} 个素材？此操作不可撤销。
              </n-popconfirm>
            </n-space>
          </div>

          <div v-if="resultsItems.length === 0 && !resultsLoading" class="results-empty">
            空空如也 — 本次采集的素材已全部删除
          </div>

          <div v-else class="results-grid">
            <div
              v-for="item in resultsItems"
              :key="item.id"
              class="result-card"
              :class="{ selected: isSelected(item.id) }"
              @click="toggleSelect(item.id)"
            >
              <img
                v-if="item.thumbnail_path"
                :src="getFileUrl(item.thumbnail_path)"
                loading="lazy"
              />
              <img
                v-else
                :src="getFileUrl(item.file_path)"
                loading="lazy"
              />
              <div class="result-check">
                <n-checkbox :checked="isSelected(item.id)" size="small" />
              </div>
            </div>
          </div>
        </n-spin>
      </div>

      <!-- 选项2: 空状态引导 -->
      <n-empty v-else :description="emptyGuideTitle" size="medium">
        <template #extra>
          <div class="empty-guide">
            <div class="empty-guide-step">
              <span class="step-number">1</span>
              <span>在上方「新建采集任务」中输入关键词，选择平台</span>
            </div>
            <div class="empty-guide-step">
              <span class="step-number">2</span>
              <span>点击「开始采集」创建任务，系统将在后台自动执行</span>
            </div>
            <div class="empty-guide-step">
              <span class="step-number">3</span>
              <span>完成后可在<a href="/">灵感库</a>中查看采集到的穿搭素材</span>
            </div>
            <div class="empty-guide-tip">
              <n-icon size="16"><!-- info --></n-icon>
              <span>提示：小红书和抖音反爬严格，采集成功率有限。推荐使用<b>浏览器插件</b>一键抓取。</span>
            </div>
          </div>
        </template>
      </n-empty>
    </n-card>
  </div>
</template>

<style scoped>
.scraper-page {
  max-width: 900px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 24px;
}

/* 选项2: 空状态引导样式 */
.empty-guide {
  max-width: 420px;
  margin: 0 auto;
  text-align: left;
}
.empty-guide-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  color: #555;
  font-size: 14px;
}
.step-number {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #2080f0;
  color: #fff;
  font-size: 12px;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
}
.empty-guide-tip {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 16px;
  padding: 10px;
  background: #f0f9eb;
  border-radius: 6px;
  color: #666;
  font-size: 12px;
}

/* 平台提示卡片内列表样式 */
:deep(.n-alert-body ul) {
  margin: 4px 0;
  padding-left: 18px;
}

/* 采集结果预览面板 */
.results-panel {
  margin-top: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  background: #fff;
}
.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
}
.results-empty {
  text-align: center;
  color: #999;
  padding: 32px 0;
  font-size: 13px;
}
.results-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  max-height: 500px;
  overflow-y: auto;
}
.result-card {
  position: relative;
  aspect-ratio: 3 / 4;
  overflow: hidden;
  border-radius: 6px;
  border: 2px solid transparent;
  cursor: pointer;
  transition: border-color 0.15s;
  background: #f5f5f5;
}
.result-card.selected {
  border-color: #2080f0;
}
.result-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.result-check {
  position: absolute;
  top: 4px;
  right: 4px;
}
</style>
