<script setup lang="ts">
/** AI 模型管理页：状态面板、模型列表、下载、分析队列、历史、参数调优。 */

import { h, ref, onMounted, computed } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()

// ===== 模型状态 =====
interface OllamaModel {
  name: string; size_bytes: number; size_display: string
  modified: string; is_active: boolean; vram_used: number; loaded: boolean
}
interface ModelListResponse { models: OllamaModel[]; active_model: string }

const models = ref<OllamaModel[]>([])
const activeModel = ref('')
const ollamaConnected = ref(false)
const statusLoading = ref(false)

// ===== 下载 =====
const downloadName = ref('')
const downloadProgress = ref(0)
const downloadTotal = ref(0)
const downloadStatus = ref('')
const downloading = ref(false)

// ===== 分析队列 =====
interface QueueStats { total: number; analyzed: number; unanalyzed: number; failed: number }
const queueStats = ref<QueueStats>({ total: 0, analyzed: 0, unanalyzed: 0, failed: 0 })

// ===== 分析历史 =====
interface HistoryItem {
  id: number; inspiration_id: string; model_name: string
  thumbnail_path: string | null; file_path: string | null
  processing_time_ms: number | null; error: string | null
  status: string; created_at: string
}
const history = ref<HistoryItem[]>([])
const historyTotal = ref(0)
const historyPage = ref(1)
const historyFilter = ref<string | null>(null)

// ===== 参数 =====
interface AiSettings { active_model: string; confidence_threshold: number; analysis_timeout: number; ollama_base_url: string }
const aiSettings = ref<AiSettings>({ active_model: '', confidence_threshold: 0.6, analysis_timeout: 60, ollama_base_url: '' })
const confThreshold = ref(0.6)
const analysisTimeout = ref(60)

// ===== 标签页 =====
const activeTab = ref('models')

onMounted(() => {
  refreshModels()
  loadQueue()
  loadHistory()
  loadSettings()
})

// ---- 模型列表 ----
async function refreshModels() {
  statusLoading.value = true
  try {
    const { data } = await apiClient.get<ModelListResponse>('/ai/models')
    models.value = data.models
    activeModel.value = data.active_model
    ollamaConnected.value = true
  } catch {
    ollamaConnected.value = false
  } finally {
    statusLoading.value = false
  }
}

async function setActiveModel(name: string) {
  try {
    await apiClient.put('/ai/models/active', { params: { model_name: name } })
    activeModel.value = name
    message.success(`已切换到 ${name}`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '切换失败')
  }
}

async function deleteModel(name: string) {
  try {
    await apiClient.delete(`/ai/models/${encodeURIComponent(name)}`)
    message.success(`已删除 ${name}`)
    refreshModels()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

// ---- 下载模型 ----
function startDownload() {
  if (!downloadName.value.trim()) return
  downloading.value = true
  downloadProgress.value = 0
  downloadTotal.value = 0
  downloadStatus.value = '连接中...'

  const baseUrl = apiClient.defaults.baseURL || '/api'
  const url = `${baseUrl}/ai/models/pull?model_name=${encodeURIComponent(downloadName.value.trim())}`
  const eventSource = new EventSource(url)

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'progress') {
        downloadProgress.value = data.completed || 0
        downloadTotal.value = data.total || 0
        downloadStatus.value = data.status || ''
      } else if (data.type === 'done') {
        downloading.value = false
        downloadStatus.value = '下载完成'
        eventSource.close()
        message.success('模型下载完成')
        refreshModels()
      } else if (data.type === 'error') {
        downloading.value = false
        eventSource.close()
        message.error(data.message || '下载失败')
      }
    } catch {}
  }
  eventSource.onerror = () => {
    if (downloading.value) {
      downloading.value = false
      eventSource.close()
      message.error('下载连接中断')
    }
  }
}

const downloadPercent = computed(() => {
  if (downloadTotal.value === 0) return 0
  return Math.round((downloadProgress.value / downloadTotal.value) * 100)
})

const downloadSize = computed(() => {
  if (downloadTotal.value === 0) return ''
  return `${formatBytes(downloadProgress.value)} / ${formatBytes(downloadTotal.value)}`
})

// ---- 分析队列 ----
async function loadQueue() {
  try {
    const { data } = await apiClient.get<QueueStats>('/ai/queue')
    queueStats.value = data
  } catch {}
}

async function retryAnalysis(id: string) {
  try {
    await apiClient.post(`/ai/retry/${id}`)
    message.success('已重新加入队列')
    loadQueue()
  } catch {}
}

// ---- 分析历史 ----
async function loadHistory() {
  try {
    const params: any = { page: historyPage.value, size: 20 }
    if (historyFilter.value) params.status = historyFilter.value
    const { data } = await apiClient.get('/ai/history', { params })
    history.value = data.items
    historyTotal.value = data.total
  } catch {}
}

function filterHistory(status: string | null) {
  historyFilter.value = status
  historyPage.value = 1
  loadHistory()
}

// ---- 参数设置 ----
async function loadSettings() {
  try {
    const { data } = await apiClient.get<AiSettings>('/ai/settings')
    aiSettings.value = data
    confThreshold.value = data.confidence_threshold
    analysisTimeout.value = data.analysis_timeout
  } catch {}
}

async function saveSettings() {
  try {
    await apiClient.put('/ai/settings', {
      params: {
        confidence_threshold: confThreshold.value,
        analysis_timeout: analysisTimeout.value,
      },
    })
    message.success('参数已保存')
  } catch {}
}

// ---- 工具函数 ----
function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(1)} ${units[i]}`
}
function formatMs(ms: number | null) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function formatDate(d: string) {
  try { return new Date(d).toLocaleString('zh-CN') } catch { return d }
}
</script>

<template>
  <div class="model-page">
    <h2>AI 模型管理</h2>

    <n-tabs v-model:value="activeTab" type="line">
      <!-- ===== Tab: 模型 ===== -->
      <n-tab-pane name="models" tab="模型管理">
        <!-- 连接状态 -->
        <n-alert :type="ollamaConnected ? 'success' : 'error'" style="margin-bottom:16px">
          {{ ollamaConnected ? `Ollama 已连接 · 活跃模型: ${activeModel}` : 'Ollama 未连接' }}
        </n-alert>

        <!-- 模型列表 -->
        <n-card title="已安装模型" size="small" style="margin-bottom:16px">
          <template #header-extra>
            <n-button size="small" @click="refreshModels" :loading="statusLoading">刷新</n-button>
          </template>
          <n-data-table
            v-if="models.length"
            :columns="[
              { title: '名称', key: 'name', width: 200 },
              { title: '大小', key: 'size_display', width: 100 },
              { title: '状态', key: 'loaded', width: 80, render: (_:any, r:OllamaModel) => r.is_active ? h('n-tag', {type:'success',size:'small'}, '活跃') : r.loaded ? h('n-tag', {type:'info',size:'small'}, '已加载') : h('n-tag', {size:'small'}, '休眠') },
              { title: '更新时间', key: 'modified', width: 160, render: (_:any, r:OllamaModel) => r.modified?.split('T')[0] },
              { title: '操作', key: 'actions', render: (_:any, r:OllamaModel) => h('span', {style:'display:flex;gap:6px'}, [
                !r.is_active ? h('n-button', {size:'tiny',onClick:()=>setActiveModel(r.name)}, '启用') : null,
                !r.is_active ? h('n-popconfirm', {onPositiveClick:()=>deleteModel(r.name)},
                  { trigger: ()=>h('n-button',{size:'tiny',type:'error',secondary:true},'删除'), default: ()=>'确定删除此模型？' }
                ) : null,
              ]) },
            ]"
            :data="models" :bordered="false" size="small"
          />
          <n-empty v-else description="暂无已安装模型" size="small" />
        </n-card>

        <!-- 下载模型 -->
        <n-card title="下载新模型" size="small">
          <n-space align="center">
            <n-input v-model:value="downloadName" placeholder="如: gemma3:4b, llava:7b" style="width:280px" />
            <n-button type="primary" @click="startDownload" :disabled="!downloadName.trim()" :loading="downloading">
              下载
            </n-button>
          </n-space>
          <div v-if="downloading || downloadStatus === '下载完成'" style="margin-top:12px">
            <n-progress type="line" :percentage="downloadPercent" :height="18" :status="downloadStatus === '下载完成' ? 'success' : undefined" />
            <p style="font-size:12px;color:#666;margin:4px 0">{{ downloadStatus }} {{ downloadSize }}</p>
          </div>
          <p style="font-size:12px;color:#999;margin-top:8px">常用模型: gemma3:4b, llava:7b, llava:13b, minicpm-v:8b</p>
        </n-card>
      </n-tab-pane>

      <!-- ===== Tab: 分析队列 ===== -->
      <n-tab-pane name="queue" tab="分析进度">
        <n-grid :cols="4" :x-gap="12" style="margin-bottom:20px">
          <n-gi><n-card size="small"><n-statistic label="总素材" :value="queueStats.total" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="已分析" :value="queueStats.analyzed" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="未分析" :value="queueStats.unanalyzed" /></n-card></n-gi>
          <n-gi><n-card size="small"><n-statistic label="失败" :value="queueStats.failed" /></n-card></n-gi>
        </n-grid>

        <div v-if="queueStats.unanalyzed > 0" style="margin-bottom:16px">
          <n-progress type="line" :percentage="Math.round(queueStats.analyzed / queueStats.total * 100)" :height="24" />
        </div>

        <!-- 分析历史 -->
        <n-card title="分析历史" size="small">
          <n-radio-group v-model:value="historyFilter" @update:value="filterHistory" size="small" style="margin-bottom:12px">
            <n-radio-button :value="null">全部</n-radio-button>
            <n-radio-button value="success">成功</n-radio-button>
            <n-radio-button value="error">失败</n-radio-button>
          </n-radio-group>

          <n-data-table
            v-if="history.length"
            :columns="[
              { title: '预览', key: 'thumbnail', width: 70, render: (_:any, r:HistoryItem) => r.thumbnail_path ? h('img', {src:getFileUrl(r.thumbnail_path), style:'width:48px;height:72px;object-fit:cover;border-radius:4px'}) : '-' },
              { title: '模型', key: 'model_name', width: 130 },
              { title: '状态', key: 'status', width: 70, render: (_:any, r:HistoryItem) => h('n-tag', {type:r.status==='success'?'success':'error',size:'small'}, r.status==='success'?'成功':'失败') },
              { title: '耗时', key: 'time', width: 80, render: (_:any, r:HistoryItem) => formatMs(r.processing_time_ms) },
              { title: '时间', key: 'created_at', width: 160, render: (_:any, r:HistoryItem) => formatDate(r.created_at) },
              { title: '操作', key: 'actions', width: 80, render: (_:any, r:HistoryItem) => r.status === 'error' ? h('n-button', {size:'tiny',onClick:()=>retryAnalysis(r.inspiration_id)}, '重试') : null },
            ]"
            :data="history" :bordered="false" size="small"
          />
          <n-empty v-else description="暂无分析记录" size="small" />
        </n-card>
      </n-tab-pane>

      <!-- ===== Tab: 参数设置 ===== -->
      <n-tab-pane name="settings" tab="参数调优">
        <n-card title="AI 分析参数" size="small" style="max-width:500px">
          <n-form label-placement="left" label-width="120">
            <n-form-item label="活跃模型">
              <n-input :value="aiSettings.active_model" readonly />
            </n-form-item>
            <n-form-item label="置信度阈值">
              <n-slider v-model:value="confThreshold" :min="0" :max="1" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" />
              <span style="margin-left:12px;font-size:13px;color:#666">{{ confThreshold.toFixed(2) }}</span>
            </n-form-item>
            <n-form-item label="分析超时 (秒)">
              <n-input-number v-model:value="analysisTimeout" :min="10" :max="300" style="width:120px" />
            </n-form-item>
            <n-form-item label="Ollama 地址">
              <n-input :value="aiSettings.ollama_base_url" readonly />
            </n-form-item>
            <n-button type="primary" @click="saveSettings">保存参数</n-button>
          </n-form>
          <p style="font-size:12px;color:#999;margin-top:12px">参数仅当前会话有效，重启后恢复默认值。<br/>如需永久修改请编辑 backend/.env 文件。</p>
        </n-card>
      </n-tab-pane>
    </n-tabs>
  </div>
</template>

<style scoped>
.model-page { max-width: 1100px; margin: 0 auto; }
.model-page h2 { margin-bottom: 16px; }
</style>
