<script setup lang="ts">
/** 新建采集任务表单：平台/模式/关键词/数量/CDP 配置，含草稿持久化与 CDP 连通性测试。 */

import { ref, computed, watch, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { copyToClipboard } from '@/utils/clipboard'
import { extractHistoryKeywords } from '@/utils/scraperKeywords'
import { useChromeManager, CHROME_STATE_LABELS, CHROME_STATE_TAG } from '@/composables/useChromeManager'

const props = defineProps<{
  /** 后端返回的默认采集数量（用户无已保存草稿时应用） */
  defaultMaxCount: number
}>()

const emit = defineEmits<{
  /** 任务创建成功 */
  (e: 'created'): void
}>()

const message = useMessage()

// Chrome 生命周期（启动/停止/状态，替代手动命令行启动）
const { chromeStatus, chromeBusy, refreshChromeStatus, startChrome, stopChrome } = useChromeManager()

onMounted(() => {
  refreshChromeStatus()
  loadHistoryKeywords()
})

/** 加载历史关键词：拉取最近 200 条已完成任务，提取去重后的关键词列表 */
async function loadHistoryKeywords() {
  try {
    const { data } = await apiClient.get('/scraper/tasks', {
      params: { sort: 'newest', size: 200 },
    })
    historyKeywords.value = extractHistoryKeywords(data.items || [])
  } catch {
    // 历史关键词加载失败不影响手动输入，静默降级
  }
}

// 表单字段
const formPlatform = ref('xiaohongshu')
/** 关键词（多选）：可选历史关键词，也可手动输入新关键词（逗号/顿号分隔批量创建） */
const formKeywords = ref<string[]>([])
const formMaxCount = ref(100)
const formCdp = ref(true)
const formCdpPort = ref(9222)
const formSortMode = ref('general')  // general | latest | popular（仅小红书搜索生效）

// 历史关键词：来自已完成采集任务（最近使用优先去重），供下拉选择
const historyKeywords = ref<string[]>([])
const keywordOptions = computed(() =>
  historyKeywords.value.map((k) => ({ label: k, value: k })),
)

/** 手动输入创建关键词：支持逗号/顿号分隔一次创建多个（粘贴「连衣裙,半身裙」场景） */
function onCreateKeyword(label: string) {
  const parts = label.split(/[,，、]/).map((s) => s.trim()).filter(Boolean)
  return parts.length > 0 ? parts : null
}

// 新建任务表单草稿：初始化时从 localStorage 恢复，为空则用默认值
const hasFormDraft = localStorage.getItem('scraper-form-draft') !== null
const savedFormDraft = localStorage.getItem('scraper-form-draft')
if (savedFormDraft) {
  try {
    const draft = JSON.parse(savedFormDraft) as Record<string, unknown>
    if (draft.platform === 'xiaohongshu' || draft.platform === 'douyin') formPlatform.value = draft.platform
    // 草稿兼容：新格式为数组；旧格式为逗号分隔字符串
    if (Array.isArray(draft.keywords)) {
      formKeywords.value = draft.keywords.filter((k): k is string => typeof k === 'string')
    } else if (typeof draft.keywords === 'string') {
      formKeywords.value = draft.keywords.split(',').map((k) => k.trim()).filter(Boolean)
    }
    if (typeof draft.maxCount === 'number') formMaxCount.value = draft.maxCount
    if (typeof draft.sortMode === 'string') formSortMode.value = draft.sortMode
    if (typeof draft.cdp === 'boolean') formCdp.value = draft.cdp
    if (typeof draft.cdpPort === 'number') formCdpPort.value = draft.cdpPort
  } catch { /* 草稿损坏则忽略，使用默认值 */ }
}

// 表单草稿持久化：字段变化时自动保存（仅存草稿，不自动提交任务）
watch([formPlatform, formKeywords, formMaxCount, formCdp, formCdpPort, formSortMode], () => {
  localStorage.setItem('scraper-form-draft', JSON.stringify({
    platform: formPlatform.value,
    keywords: formKeywords.value,
    maxCount: formMaxCount.value,
    sortMode: formSortMode.value,
    cdp: formCdp.value,
    cdpPort: formCdpPort.value,
  }))
})

// 后端默认数量：仅在用户没有已保存草稿时应用
watch(() => props.defaultMaxCount, (v) => {
  if (v && !hasFormDraft) formMaxCount.value = v
}, { immediate: true })

// CDP
const cdpChecking = ref(false)
const cdpStatus = ref<'idle' | 'ok' | 'fail'>('idle')

// 复制文本到剪贴板（复用 utils/clipboard 实现）
async function copyText(text: string) {
  const ok = await copyToClipboard(text)
  if (ok) {
    message.success('已复制')
  } else {
    message.error('复制失败')
  }
}

async function testCdp() {
  cdpChecking.value = true
  cdpStatus.value = 'idle'
  try {
    const r = await apiClient.get(`/scraper/cdp-check/${formCdpPort.value}`)
    if (r.data.available && r.data.is_google_chrome) { cdpStatus.value = 'ok'; message.success(r.data.detail) }
    else if (r.data.available) { cdpStatus.value = 'fail'; message.error('非 Google Chrome') }
    else { cdpStatus.value = 'fail'; message.warning(r.data.detail + '。请启动调试 Chrome。') }
  } catch { cdpStatus.value = 'fail' }
  finally { cdpChecking.value = false }
}

async function createTask() {
  try {
    const config: any = {
      platform: formPlatform.value,
      // 多选关键词直接为数组；手动输入的条目再按逗号/顿号拆分，兼容粘贴批量关键词
      keywords: formKeywords.value.flatMap((k) =>
        k.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
      ),
      max_count: formMaxCount.value,
    }
    // CDP 仅小红书使用：抖音走独立 Playwright 浏览器，不携带端口避免误导
    if (formPlatform.value === 'xiaohongshu') {
      config.cdp_port = formCdp.value ? formCdpPort.value : null
      if (formSortMode.value !== 'general') config.sort_mode = formSortMode.value
    }
    await apiClient.post('/scraper/tasks', config)
    emit('created')
    message.success('采集任务已创建')
  } catch (e: any) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'object' && detail?.command) {
      message.error(detail.error || '创建失败')
      setTimeout(() => copyText(detail.command), 500)
    } else { message.error(detail || '创建失败') }
  }
}
</script>

<template>
<n-card title="新建采集任务" style="margin-bottom:16px" size="small">
  <n-form label-placement="left" label-width="80" size="small">
    <n-form-item label="平台">
      <n-select v-model:value="formPlatform" :options="[{label:'小红书',value:'xiaohongshu'},{label:'抖音',value:'douyin'}]" style="width:180px" />
    </n-form-item>
    <n-form-item label="关键词">
      <n-select
        v-model:value="formKeywords"
        multiple
        filterable
        tag
        :options="keywordOptions"
        :on-create="onCreateKeyword"
        placeholder="选择历史关键词，或输入新关键词后回车（逗号/顿号分隔可一次多个）"
        style="width:100%"
      />
      <template #feedback>
        <span style="font-size:12px;color:#999">
          可直接选择最近采集使用过的关键词，也可手动输入新关键词
        </span>
      </template>
    </n-form-item>
    <n-form-item label="数量">
      <n-input-number v-model:value="formMaxCount" :min="1" :max="500" style="width:100px" />
    </n-form-item>
    <n-form-item v-if="formPlatform==='xiaohongshu'" label="排序">
      <n-select v-model:value="formSortMode" :options="[{label:'综合',value:'general'},{label:'最新',value:'latest'},{label:'最热',value:'popular'}]" style="width:120px" />
    </n-form-item>
    <n-form-item v-if="formPlatform==='xiaohongshu'" label="CDP">
      <n-switch v-model:value="formCdp" @update:value="()=>{cdpStatus='idle'}" />
      <span style="margin-left:8px;font-size:12px;color:#18a058">{{ formCdp?'连接真实 Chrome（零检测）':'Playwright 自动浏览器' }}</span>
    </n-form-item>
    <n-form-item v-if="formPlatform==='xiaohongshu' && formCdp" label="端口">
      <n-space><n-input-number v-model:value="formCdpPort" :min="9222" :max="9230" style="width:100px" />
      <n-button size="small" :loading="cdpChecking" :type="cdpStatus==='ok'?'success':cdpStatus==='fail'?'warning':'default'" @click="testCdp">{{ cdpChecking?'检测中...':cdpStatus==='ok'?'✓ 已连接':cdpStatus==='fail'?'✗ 未连接':'测试连接' }}</n-button></n-space>
    </n-form-item>
    <n-form-item v-if="formPlatform==='xiaohongshu' && formCdp" label="Chrome">
      <n-space align="center">
        <n-button
          v-if="chromeStatus?.state === 'running'"
          size="small" type="warning" ghost :loading="chromeBusy"
          @click="stopChrome"
        >停止 Chrome</n-button>
        <n-button
          v-else-if="!chromeStatus || chromeStatus.state === 'not_started'"
          size="small" type="primary" :loading="chromeBusy"
          @click="startChrome"
        >启动 Chrome</n-button>
        <n-button size="small" quaternary @click="refreshChromeStatus">刷新</n-button>
        <n-tag v-if="chromeStatus" :type="CHROME_STATE_TAG[chromeStatus.state] || 'default'" size="small">
          {{ CHROME_STATE_LABELS[chromeStatus.state] || chromeStatus.state }}
        </n-tag>
      </n-space>
    </n-form-item>
    <n-form-item v-if="formPlatform==='xiaohongshu' && formCdp && chromeStatus?.detail">
      <span style="font-size:12px;color:#999;line-height:1.6">{{ chromeStatus.detail }}</span>
    </n-form-item>
    <n-alert v-if="formPlatform==='douyin'" type="warning" style="margin-bottom:12px">
      抖音网页版功能受限，搜索结果可能为空，推荐使用浏览器插件采集。
    </n-alert>
    <n-button type="primary" @click="createTask">开始采集</n-button>
  </n-form>
</n-card>
</template>
