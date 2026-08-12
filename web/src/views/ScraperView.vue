<script setup lang="ts">
/** 采集管理页：创建/查看采集任务，管理采集源。Phase 4 完整功能。 */

import { h, ref, computed, onMounted } from 'vue'
import { NTag, NButton, useMessage } from 'naive-ui'
import apiClient from '@/api/client'

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
const formMaxCount = ref(30)
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
    message.error(e.response?.data?.detail || '创建任务失败')
  }
}

function platformName(p: string): string {
  return sources.value.find((s) => s.platform === p)?.name || p
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
          <n-switch v-model:value="formCdp" />
          <span style="margin-left: 8px; font-size: 12px; color: #18a058;">
            {{ formCdp ? '连接真实 Chrome（零检测，需先启动调试 Chrome）' : 'Playwright 启动浏览器' }}
          </span>
        </n-form-item>
        <n-form-item v-if="formCdp" label="CDP 端口">
          <n-input-number v-model:value="formCdpPort" :min="9222" :max="9230" />
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
</style>
