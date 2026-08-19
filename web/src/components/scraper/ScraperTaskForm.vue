<script setup lang="ts">
/** 新建采集任务表单：平台/模式/关键词/数量/CDP 配置，含草稿持久化与 CDP 连通性测试。 */

import { ref, computed, watch, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { copyToClipboard } from '@/utils/clipboard'
import { extractHistoryKeywords } from '@/utils/scraperKeywords'
import { getApiErrorMessage } from '@/utils/apiError'
import { bloggersApi } from '@/api/persons'
import type { Person } from '@shared/types/person'
import { useChromeManager, CHROME_STATE_LABELS, CHROME_STATE_TAG } from '@/composables/useChromeManager'

const props = defineProps<{
  /** 后端返回的默认采集数量（用户无已保存草稿时应用） */
  defaultMaxCount: number
}>()

const emit = defineEmits<{
  /** 任务创建成功 */
  (e: 'created'): void
}>()

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
/** 采集方式：search 关键词搜索（默认）| blogger 按博主采集（进博主主页提取全部内容） */
const formMode = ref<'search' | 'blogger'>('search')
/** 关键词（多选）：可选历史关键词，也可手动输入新关键词（逗号/顿号分隔批量创建） */
const formKeywords = ref<string[]>([])
const formMaxCount = ref(100)
/** 按博主采集：选中的博主 */
const formBloggerId = ref<number | null>(null)
/** 按博主采集：笔记数上限 */
const formMaxNotes = ref(30)
const formCdp = ref(true)
const formCdpPort = ref(9222)
const formSortMode = ref('general')  // general | latest | popular（仅小红书搜索生效）

// 历史关键词：来自已完成采集任务（最近使用优先去重），供下拉选择
const historyKeywords = ref<string[]>([])
const keywordOptions = computed(() =>
  historyKeywords.value.map((k) => ({ label: k, value: k })),
)

// ── 按博主采集：博主下拉（供选择采集目标）──
const bloggerOptions = ref<Array<{ label: string; value: number }>>([])
const bloggerLoading = ref(false)

/** 加载博主列表（前 100 位，按名称可搜索过滤） */
async function loadBloggers() {
  bloggerLoading.value = true
  try {
    const data = await bloggersApi.fetchList({ page: 1, size: 100 })
    bloggerOptions.value = (data.items ?? []).map((p: Person) => ({
      label: `${p.name}${p.platform === 'xiaohongshu' ? '（小红书）' : ''}`,
      value: p.id,
    }))
  } catch {
    // 博主列表加载失败不阻塞表单
  } finally {
    bloggerLoading.value = false
  }
}

/** 按博主采集仅支持小红书：切换方式时平台不符则自动修正 */
watch(formMode, (m) => {
  if (m === 'blogger' && formPlatform.value !== 'xiaohongshu') {
    formPlatform.value = 'xiaohongshu'
  }
  if (m === 'blogger' && bloggerOptions.value.length === 0) {
    loadBloggers()
  }
})

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
    if (draft.mode === 'blogger' || draft.mode === 'search') formMode.value = draft.mode
    if (typeof draft.bloggerId === 'number') formBloggerId.value = draft.bloggerId
    if (typeof draft.maxNotes === 'number') formMaxNotes.value = draft.maxNotes
  } catch { /* 草稿损坏则忽略，使用默认值 */ }
}

// 表单草稿持久化：字段变化时自动保存（仅存草稿，不自动提交任务）
watch([formPlatform, formMode, formKeywords, formMaxCount, formBloggerId, formMaxNotes, formCdp, formCdpPort, formSortMode], () => {
  localStorage.setItem('scraper-form-draft', JSON.stringify({
    platform: formPlatform.value,
    mode: formMode.value,
    keywords: formKeywords.value,
    maxCount: formMaxCount.value,
    sortMode: formSortMode.value,
    bloggerId: formBloggerId.value,
    maxNotes: formMaxNotes.value,
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
    Message.success('已复制')
  } else {
    Message.error('复制失败')
  }
}

async function testCdp() {
  cdpChecking.value = true
  cdpStatus.value = 'idle'
  try {
    const r = await apiClient.get(`/scraper/cdp-check/${formCdpPort.value}`)
    if (r.data.available && r.data.is_google_chrome) { cdpStatus.value = 'ok'; Message.success(r.data.detail) }
    else if (r.data.available) { cdpStatus.value = 'fail'; Message.error('非 Google Chrome') }
    else { cdpStatus.value = 'fail'; Message.warning(r.data.detail + '。请启动调试 Chrome。') }
  } catch { cdpStatus.value = 'fail' }
  finally { cdpChecking.value = false }
}

/** Chrome 状态标签的 Arco 预设色映射（naive 语义色 → Arco color） */
function chromeTagColor(t: string): string {
  return { success: 'green', warning: 'orange', error: 'red', info: 'arcoblue', default: 'gray' }[t] || 'gray'
}

async function createTask() {
  try {
    const config: any = {
      platform: formPlatform.value,
    }
    if (formMode.value === 'blogger') {
      // 按博主采集：进博主主页 → 笔记详情全量提取（多图/视频/正文），素材直接关联博主
      if (!formBloggerId.value) {
        Message.warning('请先选择要采集的博主')
        return
      }
      config.collect_mode = 'user'
      config.blogger_id = formBloggerId.value
      config.max_notes = formMaxNotes.value
    } else {
      // 多选关键词直接为数组；手动输入的条目再按逗号/顿号拆分，兼容粘贴批量关键词
      config.keywords = formKeywords.value.flatMap((k) =>
        k.split(/[,，、]/).map((s) => s.trim()).filter(Boolean),
      )
      config.max_count = formMaxCount.value
      if (formSortMode.value !== 'general') config.sort_mode = formSortMode.value
    }
    // CDP 仅小红书使用：抖音走独立 Playwright 浏览器，不携带端口避免误导
    if (formPlatform.value === 'xiaohongshu') {
      config.cdp_port = formCdp.value ? formCdpPort.value : null
    }
    await apiClient.post('/scraper/tasks', config)
    emit('created')
    Message.success(formMode.value === 'blogger' ? '按博主采集任务已创建' : '采集任务已创建')
  } catch (e) {
    // 特殊业务：后端 detail 可能是「带启动命令」的对象（Chrome 未启动时引导复制命令）
    const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data
      ?.detail
    if (typeof detail === 'object' && detail && (detail as { command?: string }).command) {
      const d = detail as { error?: string; command: string }
      Message.error(d.error || '创建失败')
      setTimeout(() => copyText(d.command), 500)
    } else {
      Message.error(getApiErrorMessage(e, '创建失败'))
    }
  }
}
</script>

<template>
<a-card title="新建采集任务" style="margin-bottom:16px" size="small">
  <a-form :model="{ formPlatform, formKeywords, formMaxCount, formSortMode, formCdp, formCdpPort }" label-align="left" :label-col-style="{ width: '80px' }" size="small">
    <a-form-item label="平台">
      <a-select v-model="formPlatform" :options="[{label:'小红书',value:'xiaohongshu'},{label:'抖音',value:'douyin'}]" style="width:180px" />
    </a-form-item>
    <a-form-item label="采集方式">
      <a-radio-group v-model="formMode" type="button" size="small">
        <a-radio value="search">关键词搜索</a-radio>
        <a-radio value="blogger">按博主采集</a-radio>
      </a-radio-group>
      <template #extra>
        <span style="font-size:12px;color:#999">
          {{ formMode === 'blogger' ? '进入博主主页，逐篇提取笔记全部图片/视频与正文，素材自动标记该博主' : '按关键词搜索采集封面图' }}
        </span>
      </template>
    </a-form-item>
    <a-form-item v-if="formMode === 'blogger'" label="博主">
      <a-select
        :model-value="formBloggerId ?? undefined"
        :options="bloggerOptions"
        :loading="bloggerLoading"
        allow-search
        placeholder="选择要采集的博主（可在人物管理中添加）"
        style="width: 280px"
        @change="(v: unknown) => (formBloggerId = (v as number | undefined) ?? null)"
      />
    </a-form-item>
    <a-form-item v-if="formMode === 'blogger'" label="笔记数">
      <a-input-number v-model="formMaxNotes" :min="1" :max="200" style="width:100px" />
      <span style="margin-left:8px;font-size:12px;color:#999">篇笔记（进详情页逐篇提取，建议 ≤50 防风控）</span>
    </a-form-item>
    <a-form-item v-if="formMode === 'search'" label="关键词">
      <a-select
        v-model="formKeywords"
        multiple
        allow-create
        :options="keywordOptions"
        placeholder="选择历史关键词，或输入新关键词后回车（逗号/顿号分隔提交时自动拆分）"
        style="width: 100%"
      />
      <template #extra>
        <span style="font-size:12px;color:#999">
          可直接选择最近采集使用过的关键词，也可手动输入新关键词后回车创建（回车即添加）
        </span>
      </template>
    </a-form-item>
    <a-form-item v-if="formMode === 'search'" label="数量">
      <a-input-number v-model="formMaxCount" :min="1" :max="500" style="width:100px" />
    </a-form-item>
    <a-form-item v-if="formPlatform==='xiaohongshu' && formMode === 'search'" label="排序">
      <a-select v-model="formSortMode" :options="[{label:'综合',value:'general'},{label:'最新',value:'latest'},{label:'最热',value:'popular'}]" style="width:120px" />
    </a-form-item>
    <a-form-item v-if="formPlatform==='xiaohongshu'" label="CDP">
      <a-switch v-model="formCdp" @change="()=>{cdpStatus='idle'}" />
      <span style="margin-left:8px;font-size:12px;color:#18a058">{{ formCdp?'连接真实 Chrome（零检测）':'Playwright 自动浏览器' }}</span>
    </a-form-item>
    <a-form-item v-if="formPlatform==='xiaohongshu' && formCdp" label="端口">
      <a-space><a-input-number v-model="formCdpPort" :min="9222" :max="9230" style="width:100px" />
      <a-button size="small" :loading="cdpChecking" :type="cdpStatus==='ok'||cdpStatus==='fail'?'primary':'secondary'" :status="cdpStatus==='ok'?'success':cdpStatus==='fail'?'warning':undefined" @click="testCdp">{{ cdpChecking?'检测中...':cdpStatus==='ok'?'✓ 已连接':cdpStatus==='fail'?'✗ 未连接':'测试连接' }}</a-button></a-space>
    </a-form-item>
    <a-form-item v-if="formPlatform==='xiaohongshu' && formCdp" label="Chrome">
      <a-space align="center">
        <a-button
          v-if="chromeStatus?.state === 'running'"
          size="small" type="outline" status="warning" :loading="chromeBusy"
          @click="stopChrome"
        >停止 Chrome</a-button>
        <a-button
          v-else-if="!chromeStatus || chromeStatus.state === 'not_started'"
          size="small" type="primary" :loading="chromeBusy"
          @click="startChrome"
        >启动 Chrome</a-button>
        <a-button size="small" type="text" @click="refreshChromeStatus">刷新</a-button>
        <a-tag v-if="chromeStatus" :color="chromeTagColor(CHROME_STATE_TAG[chromeStatus.state] || 'default')" size="small">
          {{ CHROME_STATE_LABELS[chromeStatus.state] || chromeStatus.state }}
        </a-tag>
      </a-space>
    </a-form-item>
    <a-form-item v-if="formPlatform==='xiaohongshu' && formCdp && chromeStatus?.detail">
      <span style="font-size:12px;color:#999;line-height:1.6">{{ chromeStatus.detail }}</span>
    </a-form-item>
    <a-alert v-if="formPlatform==='douyin'" type="warning" style="margin-bottom:12px">
      抖音网页版功能受限，搜索结果可能为空，推荐使用浏览器插件采集。
    </a-alert>
    <a-button type="primary" @click="createTask">开始采集</a-button>
  </a-form>
</a-card>
</template>
