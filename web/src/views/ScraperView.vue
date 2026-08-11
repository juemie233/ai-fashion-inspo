<script setup lang="ts">
/** 采集管理页：创建/查看采集任务，管理采集源。Phase 4 完整功能。 */

import { h, ref, onMounted } from 'vue'
import apiClient from '@/api/client'

interface ScraperTask {
  id: number
  platform: string
  status: string
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
const formMaxCount = ref(50)

onMounted(async () => {
  try {
    const [sRes, tRes] = await Promise.all([
      apiClient.get('/scraper/sources'),
      apiClient.get('/scraper/tasks'),
    ])
    sources.value = sRes.data.sources
    tasks.value = tRes.data
  } catch {
    // 采集引擎在 Phase 4 实现
  }
})

async function createTask() {
  try {
    await apiClient.post('/scraper/tasks', {
      platform: formPlatform.value,
      keywords: formKeywords.value
        .split(',')
        .map((k) => k.trim())
        .filter(Boolean),
      max_count: formMaxCount.value,
    })
    // 刷新任务列表
    const tRes = await apiClient.get('/scraper/tasks')
    tasks.value = tRes.data
  } catch {
    // 采集引擎在 Phase 4 实现
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
</script>

<template>
  <div class="scraper-page">
    <h2>采集管理</h2>
    <p class="subtitle">自动化采集小红书和抖音的穿搭内容。此功能在 Phase 4 完整实现。</p>

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
        <n-button type="primary" @click="createTask">
          开始采集
        </n-button>
      </n-form>
      <p style="color: #999; font-size: 12px; margin-top: 12px">
        ⚠️ 自动采集依赖于平台网页版，可靠性有限。推荐使用浏览器插件作为主要采集方式。
      </p>
    </n-card>

    <!-- 任务历史 -->
    <n-card title="采集任务历史">
      <n-data-table
        v-if="tasks.length > 0"
        :columns="[
          { title: '平台', key: 'platform', render: (_: any, row: ScraperTask) => platformName(row.platform) },
          { title: '状态', key: 'status', render: (_: any, row: ScraperTask) => h('n-tag', { type: statusType(row.status), size: 'small' }, statusLabel(row.status)) },
          { title: '发现数量', key: 'items_found' },
          { title: '新增数量', key: 'items_added' },
          { title: '创建时间', key: 'created_at', render: (_: any, row: ScraperTask) => new Date(row.created_at).toLocaleString('zh-CN') },
        ]"
        :data="tasks"
        :bordered="false"
        size="small"
      />
      <n-empty v-else description="暂无采集任务" size="small" />
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
</style>
