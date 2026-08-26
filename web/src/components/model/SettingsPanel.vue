<script setup lang="ts">
/** 参数调优面板：基础参数、Prompt 管理、单图测试、采样参数、数据重置。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { h, ref, onMounted, onUnmounted } from 'vue'
import { Tag, Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { useAiModelsStore } from '@/stores/aiModels'
import { useTagsStore } from '@/stores/tags'
import { formatMs } from '@/utils/format'

const store = useAiModelsStore()
const tagsStore = useTagsStore()

interface AiSettings {
  active_model: string
  confidence_threshold: number
  analysis_timeout: number
  ollama_base_url: string
  defaults: { confidence_threshold: number; analysis_timeout: number }
}
interface SamplingParams {
  temperature: number
  top_p: number
  top_k: number
  num_predict: number
  num_ctx: number
  think: boolean
  defaults: {
    temperature: number
    top_p: number
    top_k: number
    num_predict: number
    num_ctx: number
    think: boolean
  }
}
const aiSettings = ref<AiSettings>({
  active_model: '',
  confidence_threshold: 0.6,
  analysis_timeout: 60,
  ollama_base_url: '',
  defaults: { confidence_threshold: 0.6, analysis_timeout: 60 },
})
const confThreshold = ref(0.6)
const analysisTimeout = ref(60)
const samplingParams = ref<SamplingParams>({
  temperature: 0.7,
  top_p: 0.9,
  top_k: 40,
  num_predict: 1024,
  num_ctx: 16384,
  think: false,
  defaults: {
    temperature: 0.7,
    top_p: 0.9,
    top_k: 40,
    num_predict: 1024,
    num_ctx: 16384,
    think: false,
  },
})
// 全局默认值由后端下发（.env 实际值），避免前端硬编码与后端不一致
const defaultSettings = ref({ confidence_threshold: 0.6, analysis_timeout: 60 })
const defaultSampling = ref({
  temperature: 0.7,
  top_p: 0.9,
  top_k: 40,
  num_predict: 1024,
  num_ctx: 16384,
  think: false,
})
const savingSettings = ref(false)

// ===== Prompt =====
const currentPrompt = ref('')
const editedPrompt = ref('')
const promptLoading = ref(false)
const promptSaving = ref(false)

interface PromptVersion {
  prompt: string
  saved_at: string
  length: number
}
const promptVersions = ref<PromptVersion[]>([])
const promptVersionsVisible = ref(false)

async function loadPromptVersions() {
  try {
    const { data } = await apiClient.get<{ versions: PromptVersion[]; current: string }>(
      '/ai/prompt/versions',
    )
    promptVersions.value = data.versions
  } catch {
    /* 静默 */
  }
}

async function savePromptVersion() {
  try {
    if (editedPrompt.value !== currentPrompt.value) {
      await apiClient.put('/ai/prompt', { prompt: editedPrompt.value })
      currentPrompt.value = editedPrompt.value
    }
    await apiClient.post('/ai/prompt/save-version')
    Message.success('版本已保存')
    loadPromptVersions()
  } catch {
    Message.error('保存版本失败')
  }
}

async function rollbackPrompt(index: number) {
  try {
    const { data } = await apiClient.post('/ai/prompt/rollback', { index })
    editedPrompt.value = data.prompt
    currentPrompt.value = data.prompt
    Message.success(data.message)
    loadPromptVersions()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '回滚失败'))
  }
}

// ===== 单图测试 =====
const testInspirationId = ref('')
const testFile = ref<File | null>(null)
const testLoading = ref(false)
const testRawResponse = ref('')
const testParsed = ref<Record<string, any> | null>(null)
const testElapsedMs = ref(0)
const testModel = ref('')
const testCustomPrompt = ref('')

let testAbortController: AbortController | null = null

/** 上传图片后清空素材 ID（二者互斥，优先使用上传图片） */
function onTestFileChange(_fileList: unknown, fileItem: { file?: File | null }) {
  testFile.value = fileItem?.file || null
  if (testFile.value) testInspirationId.value = ''
}

function clearTestFile() {
  testFile.value = null
}

async function testAnalyze() {
  if (!testInspirationId.value.trim() && !testFile.value) return
  testLoading.value = true
  testRawResponse.value = ''
  testParsed.value = null
  testElapsedMs.value = 0
  testModel.value = ''

  try {
    const baseUrl = apiClient.defaults.baseURL || '/api'
    testAbortController = new AbortController()

    // 优先使用上传图片（multipart），否则回退到素材 ID
    let response: Response
    if (testFile.value) {
      const query = testCustomPrompt.value.trim()
        ? `?custom_prompt=${encodeURIComponent(testCustomPrompt.value.trim())}`
        : ''
      const form = new FormData()
      form.append('file', testFile.value)
      response = await fetch(`${baseUrl}/ai/test-analyze${query}`, {
        method: 'POST',
        body: form,
        signal: testAbortController.signal,
      })
    } else {
      const params = new URLSearchParams({ inspiration_id: testInspirationId.value.trim() })
      if (testCustomPrompt.value.trim()) params.set('custom_prompt', testCustomPrompt.value.trim())
      response = await fetch(`${baseUrl}/ai/test-analyze?${params}`, {
        method: 'POST',
        signal: testAbortController.signal,
      })
    }

    if (!response.ok) {
      const err = await response.json()
      throw new Error(err.detail || '测试请求失败')
    }

    const reader = response.body?.getReader()
    if (!reader) throw new Error('无法读取响应流')

    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'done') {
              testRawResponse.value = data.raw_response || ''
              testParsed.value = data.parsed || {}
              testElapsedMs.value = data.elapsed_ms || 0
              testModel.value = data.model || ''
              Message.success(`测试完成 (${data.elapsed_ms}ms)`)
            } else if (data.type === 'error') {
              Message.error(data.message || '测试失败')
            }
          } catch {}
        }
      }
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '测试请求中断'))
  } finally {
    testLoading.value = false
  }
}

// ===== 重置所有数据 =====
// 三步确认：按钮 → 第一次 popconfirm → 输入 DELETE 强确认
const resetStep = ref(0)
const resetting = ref(false)
const resetConfirmText = ref('')
const RESET_CONFIRM_WORD = 'DELETE'

function startReset() {
  resetStep.value = 1
}
function cancelReset() {
  resetStep.value = 0
  resetConfirmText.value = ''
}

// 第一次确认通过 → 进入输入 DELETE 的最终确认
function confirmResetStep() {
  if (resetStep.value === 1) {
    resetStep.value = 2
  }
}

// 最终确认：输入正确文字后才真正调用重置接口
async function executeReset() {
  if (resetConfirmText.value !== RESET_CONFIRM_WORD) return
  resetStep.value = 0
  resetting.value = true
  try {
    const { data } = await apiClient.delete('/ai/reset', {
      params: { confirm: 'yes', confirm_text: RESET_CONFIRM_WORD },
    })
    Message.success(
      data.snapshot
        ? `${data.message || '所有数据已重置'}（已自动保留 reset 前快照 7 天）`
        : data.message || '所有数据已重置',
    )
    store.refreshModels()
    loadSettings()
    loadSamplingParams()
    loadPrompt()
    tagsStore.load(true)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重置失败'))
  } finally {
    resetting.value = false
    resetConfirmText.value = ''
  }
}

// ===== 参数加载/保存 =====
async function loadSettings() {
  try {
    const { data } = await apiClient.get<AiSettings>('/ai/settings')
    aiSettings.value = data
    confThreshold.value = data.confidence_threshold
    analysisTimeout.value = data.analysis_timeout
    defaultSettings.value = data.defaults
  } catch {}
}

async function loadSamplingParams() {
  try {
    const { data } = await apiClient.get<SamplingParams>('/ai/sampling-params')
    samplingParams.value = data
    defaultSampling.value = data.defaults
  } catch {}
}

async function saveSettings() {
  savingSettings.value = true
  try {
    await apiClient.put('/ai/settings', null, {
      params: {
        confidence_threshold: confThreshold.value,
        analysis_timeout: analysisTimeout.value,
      },
    })
    await apiClient.put('/ai/sampling-params', null, {
      params: {
        temperature: samplingParams.value.temperature,
        top_p: samplingParams.value.top_p,
        top_k: samplingParams.value.top_k,
        num_predict: samplingParams.value.num_predict,
        num_ctx: samplingParams.value.num_ctx,
        think: samplingParams.value.think,
      },
    })
    Message.success('参数已保存（按模型独立持久化，重启后仍生效）')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存失败'))
  } finally {
    savingSettings.value = false
  }
}

function resetToDefaults() {
  confThreshold.value = defaultSettings.value.confidence_threshold
  analysisTimeout.value = defaultSettings.value.analysis_timeout
  samplingParams.value = { ...samplingParams.value, ...defaultSampling.value }
  Message.info('已恢复为全局默认值（需点击保存生效）')
}

// ===== 清除本模型自定义配置 =====
const clearingModelConfig = ref(false)

async function clearModelConfig() {
  clearingModelConfig.value = true
  try {
    const { data } = await apiClient.delete<{ message: string }>('/ai/model-config')
    Message.success(data.message || '已恢复全局默认值')
    loadSettings()
    loadSamplingParams()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '清除失败'))
  } finally {
    clearingModelConfig.value = false
  }
}

// ===== 每模型配置总览 =====
interface ModelConfigOverviewItem {
  name: string
  has_config: boolean
  config_fields: string[]
  has_prompt: boolean
  prompt_length: number
}
const configOverview = ref<ModelConfigOverviewItem[]>([])
const configOverviewLoading = ref(false)

async function loadConfigOverview() {
  configOverviewLoading.value = true
  try {
    const { data } = await apiClient.get<{ models: ModelConfigOverviewItem[] }>(
      '/ai/model-config/overview',
    )
    configOverview.value = data.models
  } catch {
    /* 静默 */
  } finally {
    configOverviewLoading.value = false
  }
}

// ===== 跨模型复制配置 =====
const copyConfigSource = ref<string | null>(null)
const copyConfigDestination = ref('')
const copyConfigLoading = ref(false)

async function copyModelConfig() {
  const source = copyConfigSource.value
  const dest = copyConfigDestination.value.trim()
  if (!source || !dest) {
    Message.warning('请选择源模型并输入目标模型名')
    return
  }
  copyConfigLoading.value = true
  try {
    const { data } = await apiClient.post<{ message: string }>('/ai/model-config/copy', {
      source,
      destination: dest,
    })
    Message.success(data.message)
    loadConfigOverview()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '复制失败'))
  } finally {
    copyConfigLoading.value = false
  }
}

async function loadPrompt() {
  promptLoading.value = true
  try {
    const { data } = await apiClient.get<{ prompt: string; length: number }>('/ai/prompt')
    currentPrompt.value = data.prompt
    editedPrompt.value = data.prompt
  } catch {
    /* 忽略 */
  } finally {
    promptLoading.value = false
  }
}

async function savePrompt() {
  promptSaving.value = true
  try {
    await apiClient.put('/ai/prompt', { prompt: editedPrompt.value })
    currentPrompt.value = editedPrompt.value
    Message.success('Prompt 已更新（按模型持久化保存）')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存 Prompt 失败'))
  } finally {
    promptSaving.value = false
  }
}

function resetPrompt() {
  editedPrompt.value = currentPrompt.value
  Message.info('已恢复为上次保存的 Prompt')
}

async function handleSetActiveModel(name: string) {
  const ok = await store.setActiveModel(name)
  if (ok) {
    Message.success(`已切换到 ${name}`)
    loadSettings()
    loadSamplingParams()
    loadPrompt() // 切换模型后加载该模型的独立 Prompt
  } else {
    Message.error('切换失败')
  }
}

onMounted(() => {
  loadSettings()
  loadSamplingParams()
  loadPrompt()
  loadConfigOverview()
})

onUnmounted(() => {
  if (testAbortController) testAbortController.abort()
})

/** 每模型配置总览列定义 */
const configColumns = [
  { title: '模型', dataIndex: 'name', width: 180 },
  {
    title: '参数覆盖',
    dataIndex: 'config',
    render: ({ record }: { record: unknown }) => {
      const r = record as ModelConfigOverviewItem
      return r.has_config
        ? h(
            'span',
            { style: 'display:flex;flex-wrap:wrap;gap:2px' },
            r.config_fields.map((f: string) => h(Tag, { size: 'small' }, () => f)),
          )
        : '-'
    },
  },
  {
    title: 'Prompt',
    dataIndex: 'prompt',
    render: ({ record }: { record: unknown }) => {
      const r = record as ModelConfigOverviewItem
      return r.has_prompt
        ? h(Tag, { color: 'arcoblue', size: 'small' }, () => `已自定义 (${r.prompt_length} 字符)`)
        : '-'
    },
  },
]
</script>

<template>
  <a-space direction="vertical" :size="16" style="max-width: none; width: 100%">
    <!-- 基础参数 -->
    <a-card title="基础参数" size="small">
      <a-form
        :model="{ confThreshold, analysisTimeout }"
        label-align="left"
        :label-col-style="{ width: '110px' }"
      >
        <a-form-item label="活跃模型">
          <a-select
            :model-value="store.activeModel"
            :options="store.models.map((m) => ({ label: m.name, value: m.name }))"
            placeholder="选择模型"
            allow-search
            @change="(v: unknown) => handleSetActiveModel(String(v))"
          />
        </a-form-item>
        <a-form-item label="置信度阈值">
          <a-slider
            v-model="confThreshold"
            :min="0"
            :max="1"
            :step="0.05"
            :format-tooltip="(v: number) => v.toFixed(2)"
            style="max-width: 280px"
          />
          <span style="margin-left: 12px; font-size: 13px; color: #666">{{
            confThreshold.toFixed(2)
          }}</span>
        </a-form-item>
        <a-form-item label="分析超时 (秒)">
          <a-input-number v-model="analysisTimeout" :min="10" :max="300" style="width: 120px" />
        </a-form-item>
        <a-form-item label="Ollama 地址">
          <a-input :model-value="aiSettings.ollama_base_url" readonly />
        </a-form-item>
      </a-form>
    </a-card>

    <!-- Prompt 编辑 -->
    <a-card title="分析 Prompt" size="small">
      <a-spin :loading="promptLoading" style="display: block">
        <a-textarea
          v-model="editedPrompt"
          :auto-size="{ minRows: 8, maxRows: 20 }"
          placeholder="输入 AI 分析 prompt..."
          style="font-family: monospace; font-size: 13px"
        />
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 12px;
          "
        >
          <a-space align="center">
            <a-button type="primary" size="small" @click="savePrompt" :loading="promptSaving"
              >保存 Prompt</a-button
            >
            <a-button size="small" @click="resetPrompt">撤销修改</a-button>
          </a-space>
          <span style="font-size: 12px; color: #999">按模型独立保存（prompt_configs.json）</span>
        </div>
        <p style="font-size: 11px; color: #999; margin-top: 8px">
          修改 prompt 仅影响当前模型「{{ store.activeModel }}」后续的 AI
          分析结果（按模型隔离）。改动后建议先用「单图测试」验证效果。
        </p>

        <a-button
          size="mini"
          style="margin-top: 8px"
          @click="
            savePromptVersion()
            promptVersionsVisible = true
            loadPromptVersions()
          "
          >保存版本</a-button
        >
        <a-button
          size="mini"
          style="margin-top: 8px; margin-left: 6px"
          @click="
            promptVersionsVisible = !promptVersionsVisible
            loadPromptVersions()
          "
        >
          {{ promptVersionsVisible ? '隐藏历史' : '版本历史' }}
          {{ promptVersions.length > 0 ? `(${promptVersions.length})` : '' }}
        </a-button>

        <div
          v-if="promptVersionsVisible && promptVersions.length > 0"
          class="prompt-versions"
          style="margin-top: 8px; max-height: 200px; overflow-y: auto"
        >
          <div
            v-for="(v, i) in promptVersions"
            :key="i"
            style="
              display: flex;
              align-items: center;
              justify-content: space-between;
              padding: 4px 8px;
              background: #f5f5f5;
              border-radius: 4px;
              margin-bottom: 4px;
              font-size: 12px;
            "
          >
            <span
              >版本 #{{ promptVersions.length - i }} — {{ v.saved_at?.split('T')[0] }}
              {{ v.saved_at?.split('T')[1]?.slice(0, 5) }} ({{ v.length }} 字符)</span
            >
            <a-button size="mini" @click="rollbackPrompt(i)">回滚</a-button>
          </div>
        </div>
        <div
          v-else-if="promptVersionsVisible"
          style="font-size: 12px; color: #999; margin-top: 4px"
        >
          暂无版本历史，修改 prompt 后点击「保存版本」创建
        </div>
      </a-spin>
    </a-card>

    <!-- 单图测试 -->
    <a-card title="单图即时测试" size="small">
      <p style="font-size: 12px; color: #999; margin-bottom: 12px">
        使用当前 prompt 和参数对单张图片进行测试分析，不保存记录，不影响正式数据。
      </p>
      <a-space align="center" style="margin-bottom: 12px" :wrap="false">
        <a-upload :show-file-list="false" accept="image/*" @change="onTestFileChange">
          <a-button size="small" type="secondary">上传图片</a-button>
        </a-upload>
        <span v-if="testFile" style="font-size: 12px; color: #666">
          已选：{{ testFile.name }}
          <a-button size="mini" type="text" status="danger" @click="clearTestFile">移除</a-button>
        </span>
        <span v-else style="font-size: 12px; color: #999">或</span>
        <a-input
          v-model="testInspirationId"
          placeholder="输入素材 ID 或完整 UUID"
          style="width: 260px"
          size="small"
          :disabled="!!testFile"
        />
        <a-button
          type="primary"
          size="small"
          @click="testAnalyze"
          :loading="testLoading"
          :disabled="!testInspirationId.trim() && !testFile"
        >
          {{ testLoading ? '分析中...' : '开始测试' }}
        </a-button>
      </a-space>
      <a-textarea
        v-model="testCustomPrompt"
        :auto-size="{ minRows: 2, maxRows: 6 }"
        placeholder="可选：临时覆盖 prompt（留空则使用上方保存的 prompt）"
        size="small"
        style="font-family: monospace; font-size: 12px; margin-bottom: 12px"
      />
      <div v-if="testRawResponse || testLoading" style="margin-top: 8px">
        <a-alert v-if="testModel" type="success" style="margin-bottom: 8px">
          <template #title
            >测试完成 — 模型: {{ testModel }} · 耗时: {{ formatMs(testElapsedMs) }}</template
          >
        </a-alert>
        <a-collapse>
          <a-collapse-item header="解析结果">
            <div v-if="testParsed && Object.keys(testParsed).length">
              <div v-for="(val, key) in testParsed" :key="key" style="margin-bottom: 6px">
                <a-tag color="arcoblue" size="small" style="margin-right: 4px">{{ key }}</a-tag>
                <span style="font-size: 12px; word-break: break-all">{{
                  JSON.stringify(val)
                }}</span>
              </div>
            </div>
            <a-empty v-else description="未能解析出结构化结果" />
          </a-collapse-item>
          <a-collapse-item header="原始响应">
            <pre
              style="
                font-size: 12px;
                white-space: pre-wrap;
                word-break: break-all;
                background: #f5f5f5;
                padding: 12px;
                border-radius: 6px;
                margin: 0;
              "
              >{{ testRawResponse }}</pre>
          </a-collapse-item>
        </a-collapse>
      </div>
    </a-card>

    <!-- 采样参数 -->
    <a-card title="采样参数" size="small">
      <a-form :model="samplingParams" label-align="left" :label-col-style="{ width: '110px' }">
        <a-form-item label="Temperature">
          <a-slider
            v-model="samplingParams.temperature"
            :min="0"
            :max="2"
            :step="0.05"
            :format-tooltip="(v: number) => v.toFixed(2)"
            style="max-width: 280px"
          />
          <span style="margin-left: 12px; font-size: 13px; color: #666">{{
            samplingParams.temperature.toFixed(2)
          }}</span>
        </a-form-item>
        <a-form-item label="Top P">
          <a-slider
            v-model="samplingParams.top_p"
            :min="0"
            :max="1"
            :step="0.05"
            :format-tooltip="(v: number) => v.toFixed(2)"
            style="max-width: 280px"
          />
          <span style="margin-left: 12px; font-size: 13px; color: #666">{{
            samplingParams.top_p.toFixed(2)
          }}</span>
        </a-form-item>
        <a-form-item label="Top K">
          <a-input-number v-model="samplingParams.top_k" :min="1" :max="100" style="width: 120px" />
        </a-form-item>
        <a-form-item label="Max Tokens">
          <a-input-number
            v-model="samplingParams.num_predict"
            :min="64"
            :max="8192"
            :step="64"
            style="width: 140px"
          />
        </a-form-item>
        <a-form-item label="上下文窗口">
          <a-input-number
            v-model="samplingParams.num_ctx"
            :min="1024"
            :max="131072"
            :step="1024"
            style="width: 160px"
          />
          <span style="margin-left: 12px; font-size: 13px; color: #666"
            >视觉模型编码图片消耗大量 token，过小会截断输出（建议 ≥ 8192）</span
          >
        </a-form-item>
        <a-form-item label="思考模式">
          <a-switch v-model="samplingParams.think" />
          <span style="margin-left: 12px; font-size: 13px; color: #666"
            >思考模型开启后更慢，可能提升推理质量（仅思考型模型生效）</span
          >
        </a-form-item>
      </a-form>
    </a-card>

    <!-- 每模型配置总览 -->
    <a-card title="每模型配置总览" size="small">
      <p style="font-size: 12px; color: #999; margin: 0 0 12px">
        一览 model_configs.json / prompt_configs.json 中哪些模型有自定义参数或
        Prompt，支持跨模型复制。
      </p>
      <a-table
        v-if="configOverview.length"
        :columns="configColumns"
        :data="configOverview"
        :bordered="false"
        size="small"
        :pagination="false"
      />
      <a-empty v-else description="暂无模型自定义配置" />

      <a-divider style="margin: 16px 0" />
      <p style="font-size: 12px; color: #666; margin: 0 0 8px">
        跨模型复制配置：将源模型的参数与 Prompt 一并复制到目标模型。
      </p>
      <a-space align="center">
        <a-select
          :model-value="copyConfigSource ?? undefined"
          :options="store.models.map((m) => ({ label: m.name, value: m.name }))"
          placeholder="选择源模型"
          allow-search
          style="width: 220px"
          @change="(v: unknown) => (copyConfigSource = (v as string | undefined) ?? null)"
        />
        <a-input
          v-model="copyConfigDestination"
          placeholder="目标模型名（如 qwen3-vl:latest）"
          style="width: 240px"
        />
        <a-button
          type="primary"
          size="small"
          :loading="copyConfigLoading"
          :disabled="!copyConfigSource || !copyConfigDestination.trim()"
          @click="copyModelConfig"
          >复制配置</a-button
        >
      </a-space>
    </a-card>

    <!-- 操作 -->
    <a-card size="small" title="保存与重置">
      <p style="font-size: 12px; color: #999; margin: 0 0 12px">
        参数始终按模型独立持久化（超时/采样参数存 model_configs.json，置信度阈值存
        .env），重启后仍生效。
      </p>
      <a-space align="center">
        <a-button type="primary" @click="saveSettings" :loading="savingSettings">保存参数</a-button>
        <a-button @click="resetToDefaults">恢复默认值</a-button>
        <a-popconfirm
          content="删除当前模型在 model_configs.json 中的全部覆盖项，直接回退到全局默认值。确定继续？"
          @ok="clearModelConfig"
        >
          <a-button type="secondary" :loading="clearingModelConfig">清除本模型自定义配置</a-button>
        </a-popconfirm>
      </a-space>
      <p style="font-size: 12px; color: #999; margin: 8px 0 0">
        「恢复默认值」仅把表单改回全局默认（需再点保存）；「清除自定义配置」会删除已保存的覆盖项并立即生效。
      </p>
    </a-card>

    <!-- 危险操作：重置所有数据 -->
    <a-card title="⚠ 危险操作" size="small" style="border-color: #ef4444">
      <p style="font-size: 13px; color: #999; margin-bottom: 12px">
        删除数据库中所有素材、标签、分析记录，并清空所有照片文件。此操作不可恢复！
      </p>

      <a-button v-if="resetStep === 0" status="danger" @click="startReset">重置所有数据</a-button>

      <a-popconfirm
        v-if="resetStep === 1"
        content="此操作将清空数据库和所有照片文件！请再次确认。"
        @ok="confirmResetStep"
        @cancel="cancelReset"
      >
        <a-button status="danger" :loading="resetting">第一次确认：确定要删除所有数据吗？</a-button>
      </a-popconfirm>

      <div v-if="resetStep === 2" style="margin-top: 8px">
        <p style="font-size: 13px; color: #ef4444; margin-bottom: 8px">
          最后确认：此操作不可恢复！请在下方输入
          <code style="color: #ef4444; font-weight: 600">{{ RESET_CONFIRM_WORD }}</code>
          以继续。
        </p>
        <a-space>
          <a-input
            v-model="resetConfirmText"
            :placeholder="`输入 ${RESET_CONFIRM_WORD}`"
            style="width: 160px"
            allow-clear
            @press-enter="executeReset"
          />
          <a-button
            type="primary"
            status="danger"
            :disabled="resetConfirmText !== RESET_CONFIRM_WORD"
            :loading="resetting"
            @click="executeReset"
            >确认重置</a-button
          >
          <a-button :disabled="resetting" @click="cancelReset">取消</a-button>
        </a-space>
      </div>

      <p v-if="resetting" style="font-size: 12px; color: #ef4444; margin-top: 8px">
        正在删除所有数据...
      </p>
    </a-card>
  </a-space>
</template>
