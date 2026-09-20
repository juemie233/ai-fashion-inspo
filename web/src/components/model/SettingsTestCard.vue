<script setup lang="ts">
/** 参数调优 → 「单图即时测试」卡片：用当前 prompt/参数试跑单张图，不保存记录。
 *
 * 从 SettingsPanel 原样抽出（提取式重构，行为不变）。这块自成一体：状态、请求、
 * 流式解析与「离开页面中断请求」的清理全部随卡片走，父组件不再持有任何测试态。
 */

import { ref, onUnmounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'
import { formatMs } from '@/utils/format'

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

// 离开页面时中断进行中的测试请求（原先由父组件的 onUnmounted 负责）
onUnmounted(() => {
  if (testAbortController) testAbortController.abort()
})
</script>

<template>
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
              <span style="font-size: 12px; word-break: break-all">{{ JSON.stringify(val) }}</span>
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
</template>
