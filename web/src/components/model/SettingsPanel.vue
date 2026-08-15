<script setup lang="ts">
/** 参数调优面板：基础参数、Prompt 管理、单图测试、采样参数、数据重置。 */

import { ref, onMounted, onUnmounted } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { useAiModelsStore } from '@/stores/aiModels'
import { useTagsStore } from '@/stores/tags'
import { formatMs } from '@/utils/format'

const message = useMessage()
const store = useAiModelsStore()
const tagsStore = useTagsStore()

interface AiSettings { active_model: string; confidence_threshold: number; analysis_timeout: number; ollama_base_url: string }
interface SamplingParams { temperature: number; top_p: number; top_k: number; num_predict: number; num_ctx: number; think: boolean }
const aiSettings = ref<AiSettings>({ active_model: '', confidence_threshold: 0.6, analysis_timeout: 60, ollama_base_url: '' })
const confThreshold = ref(0.6)
const analysisTimeout = ref(60)
const samplingParams = ref<SamplingParams>({ temperature: 0.7, top_p: 0.9, top_k: 40, num_predict: 1024, num_ctx: 16384, think: false })
const defaultParams = { confidence_threshold: 0.6, analysis_timeout: 60, temperature: 0.7, top_p: 0.9, top_k: 40, num_predict: 1024, num_ctx: 16384, think: false }
const persistSettings = ref(false)
const savingSettings = ref(false)

// ===== Prompt =====
const currentPrompt = ref('')
const editedPrompt = ref('')
const promptLoading = ref(false)
const promptSaving = ref(false)
const persistPrompt = ref(false)

interface PromptVersion { prompt: string; saved_at: string; length: number }
const promptVersions = ref<PromptVersion[]>([])
const promptVersionsVisible = ref(false)

async function loadPromptVersions() {
  try {
    const { data } = await apiClient.get<{ versions: PromptVersion[]; current: string }>('/ai/prompt/versions')
    promptVersions.value = data.versions
  } catch { /* 静默 */ }
}

async function savePromptVersion() {
  try {
    if (editedPrompt.value !== currentPrompt.value) {
      await apiClient.put('/ai/prompt', { prompt: editedPrompt.value, persist: false })
      currentPrompt.value = editedPrompt.value
    }
    await apiClient.post('/ai/prompt/save-version')
    message.success('版本已保存')
    loadPromptVersions()
  } catch { message.error('保存版本失败') }
}

async function rollbackPrompt(index: number) {
  try {
    const { data } = await apiClient.post('/ai/prompt/rollback', { index })
    editedPrompt.value = data.prompt
    currentPrompt.value = data.prompt
    message.success(data.message)
    loadPromptVersions()
  } catch (e: any) { message.error(e.response?.data?.detail || '回滚失败') }
}

// ===== 单图测试 =====
const testInspirationId = ref('')
const testLoading = ref(false)
const testRawResponse = ref('')
const testParsed = ref<Record<string, any> | null>(null)
const testElapsedMs = ref(0)
const testModel = ref('')
const testCustomPrompt = ref('')

let testAbortController: AbortController | null = null

async function testAnalyze() {
  if (!testInspirationId.value.trim()) return
  testLoading.value = true
  testRawResponse.value = ''
  testParsed.value = null
  testElapsedMs.value = 0
  testModel.value = ''

  try {
    const baseUrl = apiClient.defaults.baseURL || '/api'
    const params = new URLSearchParams({ inspiration_id: testInspirationId.value.trim() })
    if (testCustomPrompt.value.trim()) params.set('custom_prompt', testCustomPrompt.value.trim())

    testAbortController = new AbortController()
    const response = await fetch(`${baseUrl}/ai/test-analyze?${params}`, { method: 'POST', signal: testAbortController.signal })
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
              message.success(`测试完成 (${data.elapsed_ms}ms)`)
            } else if (data.type === 'error') {
              message.error(data.message || '测试失败')
            }
          } catch {}
        }
      }
    }
  } catch (e: any) {
    message.error(e.message || '测试请求中断')
  } finally {
    testLoading.value = false
  }
}

// ===== 重置所有数据 =====
const resetStep = ref(0)
const resetting = ref(false)

function startReset() { resetStep.value = 1 }
function cancelReset() { resetStep.value = 0 }

async function confirmResetStep() {
  if (resetStep.value === 1) {
    resetStep.value = 2
  } else if (resetStep.value === 2) {
    resetStep.value = 0
    resetting.value = true
    try {
      const { data } = await apiClient.delete('/ai/reset', { params: { confirm: 'yes' } })
      message.success(data.message || '所有数据已重置')
      store.refreshModels()
      loadSettings()
      loadSamplingParams()
      loadPrompt()
      tagsStore.load(true)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '重置失败')
    } finally {
      resetting.value = false
    }
  }
}

// ===== 参数加载/保存 =====
async function loadSettings() {
  try {
    const { data } = await apiClient.get<AiSettings>('/ai/settings')
    aiSettings.value = data
    confThreshold.value = data.confidence_threshold
    analysisTimeout.value = data.analysis_timeout
  } catch {}
}

async function loadSamplingParams() {
  try {
    const { data } = await apiClient.get<SamplingParams>('/ai/sampling-params')
    samplingParams.value = data
  } catch {}
}

async function saveSettings() {
  savingSettings.value = true
  try {
    await apiClient.put('/ai/settings', null, {
      params: {
        confidence_threshold: confThreshold.value,
        analysis_timeout: analysisTimeout.value,
        persist: persistSettings.value,
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
        persist: persistSettings.value,
      },
    })
    message.success('参数已保存' + (persistSettings.value ? '并持久化到 .env' : ''))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally {
    savingSettings.value = false
  }
}

function resetToDefaults() {
  confThreshold.value = defaultParams.confidence_threshold
  analysisTimeout.value = defaultParams.analysis_timeout
  samplingParams.value = {
    temperature: defaultParams.temperature,
    top_p: defaultParams.top_p,
    top_k: defaultParams.top_k,
    num_predict: defaultParams.num_predict,
    num_ctx: defaultParams.num_ctx,
    think: defaultParams.think,
  }
  message.info('已恢复默认值（需点击保存生效）')
}

async function loadPrompt() {
  promptLoading.value = true
  try {
    const { data } = await apiClient.get<{ prompt: string; length: number }>('/ai/prompt')
    currentPrompt.value = data.prompt
    editedPrompt.value = data.prompt
  } catch { /* 忽略 */ }
  finally { promptLoading.value = false }
}

async function savePrompt() {
  promptSaving.value = true
  try {
    await apiClient.put('/ai/prompt', { prompt: editedPrompt.value, persist: persistPrompt.value })
    currentPrompt.value = editedPrompt.value
    message.success('Prompt 已更新' + (persistPrompt.value ? '并持久化' : ''))
  } catch (e: any) {
    message.error(e.response?.data?.detail || '保存 Prompt 失败')
  } finally { promptSaving.value = false }
}

function resetPrompt() {
  editedPrompt.value = currentPrompt.value
  message.info('已恢复为上次保存的 Prompt')
}

async function handleSetActiveModel(name: string) {
  const ok = await store.setActiveModel(name)
  if (ok) {
    message.success(`已切换到 ${name}`)
    loadSettings()
    loadSamplingParams()
    loadPrompt()  // 切换模型后加载该模型的独立 Prompt
  } else {
    message.error('切换失败')
  }
}

onMounted(() => {
  loadSettings()
  loadSamplingParams()
  loadPrompt()
})

onUnmounted(() => {
  if (testAbortController) testAbortController.abort()
})
</script>

<template>
  <n-space vertical :size="16" style="max-width:560px">
    <!-- 基础参数 -->
    <n-card title="基础参数" size="small">
      <n-form label-placement="left" label-width="110">
        <n-form-item label="活跃模型">
          <n-select
            :value="store.activeModel"
            :options="store.models.map(m=>({label:m.name,value:m.name}))"
            placeholder="选择模型"
            filterable
            @update:value="handleSetActiveModel"
          />
        </n-form-item>
        <n-form-item label="置信度阈值">
          <n-slider v-model:value="confThreshold" :min="0" :max="1" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
          <span style="margin-left:12px;font-size:13px;color:#666">{{ confThreshold.toFixed(2) }}</span>
        </n-form-item>
        <n-form-item label="分析超时 (秒)">
          <n-input-number v-model:value="analysisTimeout" :min="10" :max="300" style="width:120px" />
        </n-form-item>
        <n-form-item label="Ollama 地址">
          <n-input :value="aiSettings.ollama_base_url" readonly />
        </n-form-item>
      </n-form>
    </n-card>

    <!-- Prompt 编辑 -->
    <n-card title="分析 Prompt" size="small">
      <n-spin :show="promptLoading">
        <n-input
          v-model:value="editedPrompt"
          type="textarea"
          :autosize="{ minRows: 8, maxRows: 20 }"
          placeholder="输入 AI 分析 prompt..."
          style="font-family:monospace;font-size:13px"
          @keydown.ctrl.s.prevent="savePrompt"
          @keydown.meta.s.prevent="savePrompt"
        />
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:12px">
          <n-space align="center">
            <n-button type="primary" size="small" @click="savePrompt" :loading="promptSaving">保存 Prompt</n-button>
            <n-button size="small" @click="resetPrompt">撤销修改</n-button>
          </n-space>
          <n-space align="center">
            <n-switch v-model:value="persistPrompt" size="small" />
            <span style="font-size:12px;color:#999">持久化保存</span>
          </n-space>
        </div>
        <p style="font-size:11px;color:#999;margin-top:8px">
          修改 prompt 会影响后续所有 AI 分析结果。改动后建议先用「单图测试」验证效果。
        </p>

        <n-button size="tiny" style="margin-top:8px" @click="savePromptVersion(); promptVersionsVisible = true; loadPromptVersions()">保存版本</n-button>
        <n-button size="tiny" style="margin-top:8px;margin-left:6px" @click="promptVersionsVisible = !promptVersionsVisible; loadPromptVersions()">
          {{ promptVersionsVisible ? '隐藏历史' : '版本历史' }} {{ promptVersions.length > 0 ? `(${promptVersions.length})` : '' }}
        </n-button>

        <div v-if="promptVersionsVisible && promptVersions.length > 0" class="prompt-versions" style="margin-top:8px;max-height:200px;overflow-y:auto">
          <div v-for="(v, i) in promptVersions" :key="i" style="display:flex;align-items:center;justify-content:space-between;padding:4px 8px;background:#f5f5f5;border-radius:4px;margin-bottom:4px;font-size:12px">
            <span>版本 #{{ promptVersions.length - i }} — {{ v.saved_at?.split('T')[0] }} {{ v.saved_at?.split('T')[1]?.slice(0,5) }} ({{ v.length }} 字符)</span>
            <n-button size="tiny" @click="rollbackPrompt(i)">回滚</n-button>
          </div>
        </div>
        <div v-else-if="promptVersionsVisible" style="font-size:12px;color:#999;margin-top:4px">暂无版本历史，修改 prompt 后点击「保存版本」创建</div>
      </n-spin>
    </n-card>

    <!-- 单图测试 -->
    <n-card title="单图即时测试" size="small">
      <p style="font-size:12px;color:#999;margin-bottom:12px">
        使用当前 prompt 和参数对单张图片进行测试分析，不保存记录，不影响正式数据。
      </p>
      <n-space align="center" style="margin-bottom:12px">
        <n-input v-model:value="testInspirationId" placeholder="输入素材 ID 或完整 UUID" style="width:280px" size="small" />
        <n-button type="primary" size="small" @click="testAnalyze" :loading="testLoading" :disabled="!testInspirationId.trim()">
          {{ testLoading ? '分析中...' : '开始测试' }}
        </n-button>
      </n-space>
      <n-input
        v-model:value="testCustomPrompt"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 6 }"
        placeholder="可选：临时覆盖 prompt（留空则使用上方保存的 prompt）"
        size="small"
        style="font-family:monospace;font-size:12px;margin-bottom:12px"
      />
      <div v-if="testRawResponse || testLoading" style="margin-top:8px">
        <n-alert v-if="testModel" type="success" style="margin-bottom:8px">
          <template #header>测试完成 — 模型: {{ testModel }} · 耗时: {{ formatMs(testElapsedMs) }}</template>
        </n-alert>
        <n-collapse>
          <n-collapse-item title="解析结果" name="parsed">
            <div v-if="testParsed && Object.keys(testParsed).length">
              <div v-for="(val, key) in testParsed" :key="key" style="margin-bottom:6px">
                <n-tag type="info" size="tiny" style="margin-right:4px">{{ key }}</n-tag>
                <span style="font-size:12px;word-break:break-all">{{ JSON.stringify(val) }}</span>
              </div>
            </div>
            <n-empty v-else description="未能解析出结构化结果" size="small" />
          </n-collapse-item>
          <n-collapse-item title="原始响应" name="raw">
            <n-code :code="testRawResponse" language="json" word-wrap />
          </n-collapse-item>
        </n-collapse>
      </div>
    </n-card>

    <!-- 采样参数 -->
    <n-card title="采样参数" size="small">
      <n-form label-placement="left" label-width="110">
        <n-form-item label="Temperature">
          <n-slider v-model:value="samplingParams.temperature" :min="0" :max="2" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
          <span style="margin-left:12px;font-size:13px;color:#666">{{ samplingParams.temperature.toFixed(2) }}</span>
        </n-form-item>
        <n-form-item label="Top P">
          <n-slider v-model:value="samplingParams.top_p" :min="0" :max="1" :step="0.05" :format-tooltip="(v:number)=>v.toFixed(2)" style="max-width:280px" />
          <span style="margin-left:12px;font-size:13px;color:#666">{{ samplingParams.top_p.toFixed(2) }}</span>
        </n-form-item>
        <n-form-item label="Top K">
          <n-input-number v-model:value="samplingParams.top_k" :min="1" :max="100" style="width:120px" />
        </n-form-item>
        <n-form-item label="Max Tokens">
          <n-input-number v-model:value="samplingParams.num_predict" :min="64" :max="8192" :step="64" style="width:140px" />
        </n-form-item>
        <n-form-item label="上下文窗口">
          <n-input-number v-model:value="samplingParams.num_ctx" :min="1024" :max="131072" :step="1024" style="width:160px" />
          <span style="margin-left:12px;font-size:13px;color:#666">视觉模型编码图片消耗大量 token，过小会截断输出（建议 ≥ 8192）</span>
        </n-form-item>
        <n-form-item label="思考模式">
          <n-switch v-model:value="samplingParams.think" />
          <span style="margin-left:12px;font-size:13px;color:#666">思考模型开启后更慢，可能提升推理质量（仅思考型模型生效）</span>
        </n-form-item>
      </n-form>
    </n-card>

    <!-- 操作 -->
    <n-card size="small">
      <n-space align="center">
        <n-switch v-model:value="persistSettings" />
        <span style="font-size:13px">持久化到 .env 文件（重启后仍生效）</span>
      </n-space>
      <n-space style="margin-top:16px">
        <n-button type="primary" @click="saveSettings" :loading="savingSettings">保存参数</n-button>
        <n-button @click="resetToDefaults">恢复默认值</n-button>
      </n-space>
    </n-card>

    <!-- 危险操作：重置所有数据 -->
    <n-card title="⚠ 危险操作" size="small" style="border-color:#ef4444">
      <p style="font-size:13px;color:#999;margin-bottom:12px">
        删除数据库中所有素材、标签、分析记录，并清空所有照片文件。此操作不可恢复！
      </p>

      <n-button v-if="resetStep === 0" type="error" @click="startReset">重置所有数据</n-button>

      <n-popconfirm v-if="resetStep === 1" @positive-click="confirmResetStep" @negative-click="cancelReset">
        <template #trigger>
          <n-button type="error" :loading="resetting">第一次确认：确定要删除所有数据吗？</n-button>
        </template>
        此操作将清空数据库和所有照片文件！请再次确认。
      </n-popconfirm>

      <n-popconfirm v-if="resetStep === 2" @positive-click="confirmResetStep" @negative-click="cancelReset">
        <template #trigger>
          <n-button type="error" secondary :loading="resetting">第二次确认：真的要删除吗？此操作不可恢复！</n-button>
        </template>
        最后一次确认：点击"确定"后将立即删除所有数据！
      </n-popconfirm>

      <p v-if="resetting" style="font-size:12px;color:#ef4444;margin-top:8px">正在删除所有数据...</p>
    </n-card>
  </n-space>
</template>
