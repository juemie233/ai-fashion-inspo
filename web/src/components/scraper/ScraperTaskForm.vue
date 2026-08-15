<script setup lang="ts">
/** 新建采集任务表单：平台/模式/关键词/数量/CDP 配置，含草稿持久化与 CDP 连通性测试。 */

import { ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

const props = defineProps<{
  /** 后端返回的默认采集数量（用户无已保存草稿时应用） */
  defaultMaxCount: number
}>()

const emit = defineEmits<{
  /** 任务创建成功 */
  (e: 'created'): void
}>()

const message = useMessage()

// 表单字段
const formPlatform = ref('xiaohongshu')
const formKeywords = ref('')
const formMaxCount = ref(100)
const formHeadless = ref(false)
const formCdp = ref(true)
const formCdpPort = ref(9222)
const formSortMode = ref('general')  // general | latest | popular
const formCollectMode = ref('search')  // search | user | topic

// 新建任务表单草稿：初始化时从 localStorage 恢复，为空则用默认值
const hasFormDraft = localStorage.getItem('scraper-form-draft') !== null
const savedFormDraft = localStorage.getItem('scraper-form-draft')
if (savedFormDraft) {
  try {
    const draft = JSON.parse(savedFormDraft) as Record<string, unknown>
    if (draft.platform === 'xiaohongshu' || draft.platform === 'douyin') formPlatform.value = draft.platform
    if (typeof draft.keywords === 'string') formKeywords.value = draft.keywords
    if (typeof draft.maxCount === 'number') formMaxCount.value = draft.maxCount
    if (typeof draft.sortMode === 'string') formSortMode.value = draft.sortMode
    if (typeof draft.collectMode === 'string') formCollectMode.value = draft.collectMode
    if (typeof draft.cdp === 'boolean') formCdp.value = draft.cdp
    if (typeof draft.cdpPort === 'number') formCdpPort.value = draft.cdpPort
  } catch { /* 草稿损坏则忽略，使用默认值 */ }
}

// 表单草稿持久化：字段变化时自动保存（仅存草稿，不自动提交任务）
watch([formPlatform, formKeywords, formMaxCount, formCdp, formCdpPort, formSortMode, formCollectMode], () => {
  localStorage.setItem('scraper-form-draft', JSON.stringify({
    platform: formPlatform.value,
    keywords: formKeywords.value,
    maxCount: formMaxCount.value,
    sortMode: formSortMode.value,
    collectMode: formCollectMode.value,
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

// copyText（修复版）
async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); message.success('已复制') }
  catch {
    try {
      const ta = document.createElement('textarea'); ta.value = text
      ta.style.cssText = 'position:fixed;left:-9999px'; document.body.appendChild(ta)
      ta.select(); document.execCommand('copy'); document.body.removeChild(ta)
      message.success('已复制')
    } catch { message.error('复制失败') }
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
      keywords: formKeywords.value.split(',').map(k => k.trim()).filter(Boolean),
      max_count: formMaxCount.value,
      headless: formHeadless.value,
      cdp_port: formCdp.value ? formCdpPort.value : null,
    }
    if (formSortMode.value !== 'general') config.sort_mode = formSortMode.value
    if (formCollectMode.value !== 'search') config.collect_mode = formCollectMode.value
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
    <n-form-item label="模式">
      <n-radio-group v-model:value="formCollectMode" size="small">
        <n-radio-button value="search">搜索</n-radio-button>
        <n-radio-button value="user">用户主页</n-radio-button>
        <n-radio-button value="topic">话题</n-radio-button>
      </n-radio-group>
    </n-form-item>
    <n-form-item :label="formCollectMode==='user'?'用户ID':'关键词'">
      <n-input v-model:value="formKeywords" :placeholder="formCollectMode==='user'?'输入用户ID或主页链接':'多个关键词用逗号分隔'" @keyup.enter="createTask" />
    </n-form-item>
    <n-form-item label="数量">
      <n-input-number v-model:value="formMaxCount" :min="1" :max="500" style="width:100px" />
    </n-form-item>
    <n-form-item v-if="formCollectMode==='search'" label="排序">
      <n-select v-model:value="formSortMode" :options="[{label:'综合',value:'general'},{label:'最新',value:'latest'},{label:'最热',value:'popular'}]" style="width:120px" />
    </n-form-item>
    <n-form-item label="CDP">
      <n-switch v-model:value="formCdp" @update:value="()=>{cdpStatus='idle'}" />
      <span style="margin-left:8px;font-size:12px;color:#18a058">{{ formCdp?'连接真实 Chrome（零检测）':'Playwright 自动浏览器' }}</span>
    </n-form-item>
    <n-form-item v-if="formCdp" label="端口">
      <n-space><n-input-number v-model:value="formCdpPort" :min="9222" :max="9230" style="width:100px" />
      <n-button size="small" :loading="cdpChecking" :type="cdpStatus==='ok'?'success':cdpStatus==='fail'?'warning':'default'" @click="testCdp">{{ cdpChecking?'检测中...':cdpStatus==='ok'?'✓ 已连接':cdpStatus==='fail'?'✗ 未连接':'测试连接' }}</n-button></n-space>
    </n-form-item>
    <n-form-item v-if="formCdp">
      <n-alert type="info" style="width:100%">
        <template #header>💡 启动调试 Chrome</template>
        <p style="margin:4px 0;font-size:12px;line-height:1.6">
          关闭所有 Chrome 窗口后在命令行执行：<br/>
          <code class="chrome-cmd" @click="copyText('C:/Users/Administrator/AppData/Local/Google/Chrome/Application/chrome.exe --remote-debugging-port=' + formCdpPort + ' --user-data-dir=C:/Users/Administrator/Desktop/chrome-scraper-profile')">
            "C:/Users/Administrator/AppData/Local/Google/Chrome/Application/chrome.exe" --remote-debugging-port={{ formCdpPort }} --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
          </code>
          启动后在 Chrome 中登录，回来点击「测试连接」即可开始采集。
        </p>
      </n-alert>
    </n-form-item>
    <n-button type="primary" @click="createTask">开始采集</n-button>
  </n-form>
</n-card>
</template>

<style scoped>
.chrome-cmd{display:block;background:#f0f0f0;padding:4px 8px;margin:4px 0;border-radius:4px;font-size:11px;cursor:pointer;user-select:all}
</style>
