<script setup lang="ts">
/** 采集管理页：Cookie管理、任务创建、日志查看、结果预览。 */

import { h, ref, computed, onMounted, onUnmounted } from 'vue'
import { NTag, NButton, NCheckbox, NSpin, NPopconfirm, useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()

// ── copyText（修复版）──
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

interface ScraperTask {
  id: number; platform: string; status: string; config: string | null
  items_found: number; items_added: number; error?: string | null; diagnostics?: string | null
  started_at?: string | null; finished_at?: string | null; created_at: string
}

interface FunnelSearch {
  keyword: string; sort_type: string
  cards_total?: number; cards_with_img?: number; cards_without_img?: number
  skipped_small?: number; skipped_icon?: number; urls_extracted?: number
  batch_added?: number; batch_skipped_existing?: number
  batch_skipped_http?: number; batch_skipped_network?: number
  error?: string
}

interface FunnelDiagnostics {
  per_search: FunnelSearch[]
  summary: {
    total_found: number; skipped_url_seen: number
    skipped_http_error: number; skipped_network_error: number
    total_added: number
  }
}

interface ScraperSource { platform: string; name: string; status: string; features: string[]; note: string }

interface CookieStatus { platform: string; exists: boolean; age_hours: number; valid: boolean; hint: string }

const sources = ref<ScraperSource[]>([])
const tasks = ref<ScraperTask[]>([])
const tombstoneCount = ref(0)
const showTombstone = ref(false)

// Cookie 状态
const cookieStatuses = ref<Record<string, CookieStatus>>({})
const showingCookieImport = ref(false)
const cookiePlatform = ref('xiaohongshu')
const cookieJsonInput = ref('')

// 表单
const formPlatform = ref('xiaohongshu')
const formKeywords = ref('')
const formMaxCount = ref(100)
const formHeadless = ref(false)
const formCdp = ref(true)
const formCdpPort = ref(9222)
const formSortMode = ref('general')  // general | latest | popular
const formCollectMode = ref('search')  // search | user | topic

// 任务筛选
const taskFilterPlatform = ref('')
const taskFilterStatus = ref('')
const taskSort = ref('newest')
const taskPage = ref(1)

// 结果预览
const resultsTaskId = ref<number | null>(null)
const resultsItems = ref<any[]>([])
const resultsTotal = ref(0)
const resultsLoading = ref(false)
const selectedIds = ref<Set<string>>(new Set())
const deletingResults = ref(false)

// CDP
const cdpChecking = ref(false)
const cdpStatus = ref<'idle' | 'ok' | 'fail'>('idle')

// 日志查看
const logTaskId = ref<number | null>(null)
const logContent = ref('')
const logLoading = ref(false)

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书', douyin: '抖音', browser_extension: '浏览器插件', scraper: '自动采集', manual_upload: '手动上传',
}
const STATUS_LABELS: Record<string, string> = { pending: '等待中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' }

function statusType(s: string): 'default'|'info'|'success'|'error'|'warning' {
  const m: Record<string, 'default'|'info'|'success'|'error'|'warning'> = {
    pending:'default',running:'info',completed:'success',failed:'error',cancelled:'warning'
  }
  return m[s]||'default'
}

function platformName(p: string) { return sources.value.find(s=>s.platform===p)?.name||PLATFORM_LABELS[p]||p }
function formatDate(d: string|null|undefined) {
  if(!d) return '-'
  try { const dt=new Date(d); return isNaN(dt.getTime())?'-':dt.toLocaleString('zh-CN') } catch { return '-' }
}
function parseKeywords(c: string|null) {
  if(!c) return '-'
  try { return (JSON.parse(c).keywords||[]).join(', ')||'-' } catch { return '-' }
}
function getTaskDuration(t: ScraperTask) {
  if(t.started_at&&t.finished_at) {
    const ms=new Date(t.finished_at).getTime()-new Date(t.started_at).getTime()
    if(ms<1000) return ms+'ms'
    if(ms<60000) return (ms/1000).toFixed(0)+'s'
    return (ms/60000).toFixed(1)+'min'
  }
  return '-'
}

// ── 数据加载 ──

async function loadAll() {
  try {
    const [sRes, tRes, cXhs, cDy] = await Promise.all([
      apiClient.get('/scraper/sources'),
      apiClient.get('/scraper/tasks', { params: { platform: taskFilterPlatform.value||undefined, status: taskFilterStatus.value||undefined, sort: taskSort.value, page: taskPage.value }}),
      apiClient.get('/scraper/cookie-status', { params: { platform: 'xiaohongshu' } }).catch(() => ({ data: { platform:'xiaohongshu', exists:false, age_hours:0, valid:false, hint:'检查失败' } })),
      apiClient.get('/scraper/cookie-status', { params: { platform: 'douyin' } }).catch(() => ({ data: { platform:'douyin', exists:false, age_hours:0, valid:false, hint:'检查失败' } })),
    ])
    sources.value = sRes.data.sources
    tasks.value = tRes.data
    tombstoneCount.value = sRes.data.tombstone_count || 0
    if (sRes.data.default_max_count) formMaxCount.value = sRes.data.default_max_count
    cookieStatuses.value = {
      xiaohongshu: cXhs.data as CookieStatus,
      douyin: cDy.data as CookieStatus,
    }
  } catch { message.error('加载失败') }
}

async function refreshTasks() {
  try { const tRes = await apiClient.get('/scraper/tasks', { params: { platform: taskFilterPlatform.value||undefined, status: taskFilterStatus.value||undefined, sort: taskSort.value, page: taskPage.value }}); tasks.value = tRes.data } catch {}
}

function onFilterChange() { taskPage.value=1; refreshTasks() }

// ── Cookie 导入 ──

async function importCookie() {
  try {
    await apiClient.post('/scraper/cookie-import', { platform: cookiePlatform.value, cookies: JSON.parse(cookieJsonInput.value) })
    message.success('Cookie 已导入')
    showingCookieImport.value = false
    cookieJsonInput.value = ''
    await loadAll()
  } catch (e:any) { message.error('导入失败: '+(e.response?.data?.detail||'JSON 格式错误')) }
}

// ── 任务操作 ──

async function createTask() {
  try {
    const config: any = {
      platform: formPlatform.value,
      keywords: formKeywords.value.split(',').map(k=>k.trim()).filter(Boolean),
      max_count: formMaxCount.value,
      headless: formHeadless.value,
      cdp_port: formCdp.value ? formCdpPort.value : null,
    }
    if (formSortMode.value !== 'general') config.sort_mode = formSortMode.value
    if (formCollectMode.value !== 'search') config.collect_mode = formCollectMode.value
    await apiClient.post('/scraper/tasks', config)
    await refreshTasks()
    message.success('采集任务已创建')
    startPollIfNeeded()
  } catch (e: any) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'object' && detail?.command) {
      message.error(detail.error || '创建失败')
      setTimeout(() => copyText(detail.command), 500)
    } else { message.error(detail || '创建失败') }
  }
}

async function cancelTask(taskId: number) {
  try { await apiClient.post(`/scraper/tasks/${taskId}/cancel`); message.success('已取消'); refreshTasks() }
  catch (e: any) { message.error(e.response?.data?.detail || '取消失败') }
}

const deletingTask = ref<number|null>(null)
async function deleteSingleTask(taskId: number) {
  try {
    deletingTask.value = taskId
    const res = await apiClient.delete(`/scraper/tasks/${taskId}`)
    if (res.status===200||res.status===204) { tasks.value = tasks.value.filter(t=>t.id!==taskId); message.success('已删除') }
  } catch (e: any) {
    if (e.response?.status===204) { tasks.value = tasks.value.filter(t=>t.id!==taskId); message.success('已删除') }
    else message.error('删除失败: '+(e.response?.data?.detail||''))
  } finally { deletingTask.value = null }
}

const clearing = ref(false)
async function clearAllTasks() {
  try { clearing.value=true; await apiClient.delete('/scraper/tasks'); tasks.value=[]; message.success('已清空') }
  catch { message.error('清空失败') } finally { clearing.value=false }
}

const retrying = ref(false)
async function retryFailedTasks() {
  try { retrying.value=true; message.success((await apiClient.post('/scraper/tasks/retry-failed')).data.message); refreshTasks() }
  catch (e: any) { message.info(e.response?.status===404?'没有失败任务':(e.response?.data?.detail||'重试失败')) }
  finally { retrying.value=false }
}

// ── 日志 ──

async function viewLog(taskId: number) {
  if (logTaskId.value === taskId) { logTaskId.value = null; logContent.value = ''; return }
  logTaskId.value = taskId; logLoading.value = true
  try { logContent.value = (await apiClient.get(`/scraper/tasks/${taskId}/log`)).data.content }
  catch { message.error('日志加载失败'); logTaskId.value = null }
  finally { logLoading.value = false }
}

// ── 漏斗视图 ──

const funnelTaskId = ref<number | null>(null)
const funnelData = ref<FunnelDiagnostics | null>(null)

/** 漏斗弹窗开关：绑定 n-modal 的 v-model:show，关闭时清空数据 */
const funnelOpen = computed({
  get: () => funnelTaskId.value !== null,
  set: (v: boolean) => { if (!v) { funnelTaskId.value = null; funnelData.value = null } },
})

function viewFunnel(task: ScraperTask) {
  if (funnelTaskId.value === task.id) { funnelTaskId.value = null; funnelData.value = null; return }
  if (!task.diagnostics) { message.warning('该任务无漏斗数据（旧版本采集的任务）'); return }
  try {
    funnelData.value = JSON.parse(task.diagnostics)
    funnelTaskId.value = task.id
  } catch { message.error('漏斗数据解析失败') }
}

function funnelPct(value: number, max: number): string {
  if (!max) return '0%'
  return Math.round(value / max * 100) + '%'
}

const BAR_COLORS = ['#5470c6','#91cc75','#fac858','#ee6666','#73c0de','#3ba272','#fc8452','#9a60b4']
function barColor(idx: number) { return BAR_COLORS[idx % BAR_COLORS.length] }

function sortLabel(s: string): string {
  const m: Record<string,string> = { general: '综合', time_descending: '最新', popularity_descending: '最热' }
  return m[s] || s
}

// ── 结果预览 ──

async function viewResults(taskId: number) {
  if (resultsTaskId.value===taskId) { resultsTaskId.value=null; resultsItems.value=[]; selectedIds.value=new Set(); return }
  resultsTaskId.value=taskId; resultsLoading.value=true; selectedIds.value=new Set()
  try { const r=await apiClient.get(`/scraper/tasks/${taskId}/results`,{params:{size:200}}); resultsItems.value=r.data.items; resultsTotal.value=r.data.total }
  catch { message.error('加载失败'); resultsTaskId.value=null } finally { resultsLoading.value=false }
}

function toggleSelect(id:string){const n=new Set(selectedIds.value);n.has(id)?n.delete(id):n.add(id);selectedIds.value=n}
function selectAll(){selectedIds.value=selectedIds.value.size===resultsItems.value.length?new Set():new Set(resultsItems.value.map((i:any)=>i.id))}

async function deleteSelected() {
  if(!selectedIds.value.size)return;deletingResults.value=true
  try{const r=await apiClient.post(`/scraper/tasks/${resultsTaskId.value}/results/batch-delete`,{ids:[...selectedIds.value]});message.success(`已删除 ${r.data.deleted_count} 个`);resultsItems.value=resultsItems.value.filter((i:any)=>!selectedIds.value.has(i.id));selectedIds.value=new Set();resultsTotal.value=r.data.remaining;if(!r.data.remaining){resultsTaskId.value=null;resultsItems.value=[]};refreshTasks()}
  catch{message.error('删除失败')}finally{deletingResults.value=false}
}

// ── CDP ──

async function testCdp() {
  cdpChecking.value=true;cdpStatus.value='idle'
  try{const r=await apiClient.get(`/scraper/cdp-check/${formCdpPort.value}`)
    if(r.data.available&&r.data.is_google_chrome){cdpStatus.value='ok';message.success(r.data.detail)}
    else if(r.data.available){cdpStatus.value='fail';message.error('非 Google Chrome')}
    else{cdpStatus.value='fail';message.warning(r.data.detail+'。请启动调试 Chrome。')}}
  catch{cdpStatus.value='fail'}finally{cdpChecking.value=false}
}

// ── 轮询 ──

const hasActiveTasks=computed(()=>tasks.value.some(t=>t.status==='pending'||t.status==='running'))
const taskStats=computed(()=>{
  const t=tasks.value;const total=t.length;const completed=t.filter(x=>x.status==='completed').length
  const failed=t.filter(x=>x.status==='failed').length;const rate=total>0?Math.round(completed/total*100):0
  return {total,completed,failed,rate}
})
const hasFailedTasks=computed(()=>tasks.value.some(t=>t.status==='failed'))

let pollTimer: ReturnType<typeof setInterval>|null=null
function startPollIfNeeded(){if(pollTimer)return;pollTimer=setInterval(async()=>{if(hasActiveTasks.value){await refreshTasks();if(!hasActiveTasks.value)stopPoll()}},5000)}
function stopPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null}}

onMounted(()=>{loadAll();startPollIfNeeded()})
onUnmounted(()=>{stopPoll()})

// ── 表格列 ──

const tableColumns = computed(() => [
  { title:'平台',key:'platform',width:80,render:(r:ScraperTask)=>platformName(r.platform) },
  { title:'关键词',key:'config',width:160,ellipsis:{tooltip:true},render:(r:ScraperTask)=>parseKeywords(r.config) },
  { title:'状态',key:'status',width:80,render:(r:ScraperTask)=>h(NTag,{type:statusType(r.status),size:'small'},STATUS_LABELS[r.status]||r.status) },
  { title:'发现',key:'items_found',width:55 },
  { title:'新增',key:'items_added',width:55 },
  { title:'耗时',key:'duration',width:70,render:(r:ScraperTask)=>getTaskDuration(r) },
  { title:'错误',key:'error',width:140,ellipsis:{tooltip:true},render:(r:ScraperTask)=>r.error?h('span',{style:{color:'#d03050',cursor:'pointer',textDecoration:'underline',fontSize:'12px'},title:r.error,onClick:()=>copyText(r.error!)},r.error.length>25?r.error.slice(0,25)+'…':r.error):'-' },
  { title:'时间',key:'created_at',width:150,render:(r:ScraperTask)=>formatDate(r.created_at) },
  { title:'操作',key:'actions',width:210,render:(r:ScraperTask)=>{
    const btns:any[]=[]
    if(r.items_added>0) btns.push(h(NButton,{size:'tiny',type:resultsTaskId.value===r.id?'warning':'primary',ghost:true,onClick:()=>viewResults(r.id)},resultsTaskId.value===r.id?'收起':'结果'))
    btns.push(h(NButton,{size:'tiny',onClick:()=>viewLog(r.id)},logTaskId.value===r.id?'关闭日志':'日志'))
    if(r.diagnostics) btns.push(h(NButton,{size:'tiny',type:funnelTaskId.value===r.id?'info':'default',ghost:true,onClick:()=>viewFunnel(r)},funnelTaskId.value===r.id?'关闭漏斗':'漏斗'))
    if(r.status==='pending'||r.status==='running') btns.push(h(NButton,{size:'tiny',type:'warning',ghost:true,onClick:()=>cancelTask(r.id)},'取消'))
    btns.push(h(NPopconfirm,{onPositiveClick:()=>deleteSingleTask(r.id)},{trigger:()=>h(NButton,{size:'tiny',type:'error',ghost:true,loading:deletingTask.value===r.id},'删除'),default:()=>'确定删除此记录？'}))
    return h('span',{style:{display:'flex',gap:'4px',flexWrap:'wrap'}},btns)
  }},
])

function expandedRowRender(row: ScraperTask) {
  return h('div',{style:{padding:'12px 24px',maxWidth:'700px'}},[
    row.config?h('div',{style:{marginBottom:'8px'}},[h('span',{style:{color:'#999',fontSize:'12px'}},'配置：'),h('pre',{style:{margin:'4px 0',fontSize:'12px',whiteSpace:'pre-wrap'}},JSON.stringify(JSON.parse(row.config),null,2))]):null,
    row.error?h('div',[h('span',{style:{color:'#d03050',fontSize:'12px'}},'错误：'),h('pre',{style:{margin:'4px 0',fontSize:'12px',color:'#d03050',whiteSpace:'pre-wrap',background:'#fef0f0',padding:'8px',borderRadius:'4px'}},row.error)]):null,
  ])
}
</script>

<template>
<div class="scraper-page">
<h2>采集管理</h2>
<p class="subtitle">自动化采集小红书和抖音的穿搭内容</p>

<!-- Cookie 状态 -->
<div class="cookie-cards">
  <n-card v-for="(cs, plat) in cookieStatuses" :key="plat" size="small" style="flex:1;min-width:260px">
    <template #header>
      {{ PLATFORM_LABELS[plat] || plat }} Cookie
      <n-tag :type="cs.valid?'success':'error'" size="small" style="margin-left:8px">{{ cs.exists?(cs.valid?'有效':'已过期'):'未配置' }}</n-tag>
    </template>
    <p style="font-size:12px;color:#666;margin:0">{{ cs.hint }}</p>
    <template #action>
      <n-button size="tiny" @click="cookiePlatform=plat;showingCookieImport=true">导入</n-button>
    </template>
  </n-card>
</div>

<!-- Cookie 导入对话框 -->
<n-modal v-model:show="showingCookieImport" preset="card" title="导入 Cookie" style="max-width:500px">
  <p style="font-size:12px;color:#666;margin-bottom:8px">粘贴 {{ PLATFORM_LABELS[cookiePlatform]||cookiePlatform }} 的 Cookie JSON 数据</p>
  <n-input v-model:value="cookieJsonInput" type="textarea" :autosize="{minRows:4,maxRows:12}" placeholder='[{"name":"...","value":"...","domain":"..."}]' style="font-family:monospace;font-size:12px" />
  <n-button type="primary" block style="margin-top:12px" @click="importCookie">导入</n-button>
</n-modal>

<!-- 采集源 -->
<n-card title="可用采集源" style="margin-bottom:16px" size="small">
  <n-list>
    <n-list-item v-for="src in sources" :key="src.platform">
      <template #prefix><n-tag :type="src.status==='available'?'success':'warning'" size="small">{{ src.status==='available'?'可用':'有限' }}</n-tag></template>
      <n-thing :title="src.name" :description="src.note">
        <template #header-extra><n-tag v-for="f in src.features" :key="f" size="tiny" :bordered="false">{{ f }}</n-tag></template>
      </n-thing>
    </n-list-item>
  </n-list>
</n-card>

<!-- 墓碑表 -->
<n-card size="small" style="margin-bottom:16px">
  <div class="tombstone-header" @click="showTombstone=!showTombstone" style="cursor:pointer;user-select:none">
    <span>{{ showTombstone?'▼':'▶' }} 已采集 URL 记录</span>
    <n-tag type="info" size="small">{{ tombstoneCount }} 个</n-tag>
  </div>
  <div v-if="showTombstone" class="tombstone-body">
    <p style="margin:8px 0 0;font-size:12px;color:#666">📌 墓碑表记录了所有曾下载过的图片 URL，采集时自动跳过，确保永不重复入库。当前共 <b>{{ tombstoneCount }}</b> 条。</p>
  </div>
</n-card>

<!-- 新建任务 -->
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
      <n-input v-model:value="formKeywords" :placeholder="formCollectMode==='user'?'输入用户ID或主页链接':'多个关键词用逗号分隔'" />
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

<!-- 平台提示 -->
<div v-for="hint in sources.map(src=>{
  let level:'warning'|'info'|'error'='info',tips:string[]=[]
  if(src.platform==='xiaohongshu'){level='warning';tips=['需要有效的登录 Cookie','反爬检测严格','搜索功能依赖页面 DOM 结构']}
  else if(src.platform==='douyin'){level='error';tips=['网页版功能严重受限','搜索结果可能为空','推荐使用浏览器插件代替']}
  else{tips=['最可靠的采集方式','支持一键抓取当前页面']}
  return {...src,level,tips}
})" :key="hint.platform" style="margin-bottom:12px">
  <n-alert :type="hint.level" :title="hint.name+' — '+(hint.level==='error'?'⚠️ 可靠性低':hint.level==='warning'?'⚡ 需要配置':'✅ 推荐使用')">
    <ul style="margin:4px 0;padding-left:18px;font-size:13px"><li v-for="t in hint.tips" :key="t">{{ t }}</li></ul>
  </n-alert>
</div>

<!-- 任务历史 -->
<n-card title="采集任务历史" size="small">
  <template #header-extra>
    <n-space align="center" size="small">
      <span v-if="taskStats.total>0" style="font-size:12px;color:#666">共 <b>{{ taskStats.total }}</b> · 成功 <b style="color:#18a058">{{ taskStats.completed }}</b> · 失败 <b style="color:#d03050">{{ taskStats.failed }}</b> · {{ taskStats.rate }}%</span>
      <n-select v-model:value="taskFilterStatus" :options="[{label:'全部状态',value:''},{label:'运行中',value:'running'},{label:'成功',value:'completed'},{label:'失败',value:'failed'}]" size="tiny" style="width:100px" @update:value="onFilterChange" />
      <n-select v-model:value="taskSort" :options="[{label:'最新',value:'newest'},{label:'最早',value:'oldest'},{label:'发现最多',value:'most_found'},{label:'新增最多',value:'most_added'}]" size="tiny" style="width:100px" @update:value="()=>{taskPage=1;refreshTasks()}" />
      <n-button v-if="hasFailedTasks" size="tiny" type="warning" ghost :loading="retrying" @click="retryFailedTasks">重试失败</n-button>
      <n-popconfirm @positive-click="clearAllTasks"><template #trigger><n-button size="tiny" :loading="clearing" type="error" ghost>清空</n-button></template>确定清空所有任务记录？</n-popconfirm>
    </n-space>
  </template>

  <n-data-table v-if="tasks.length" :columns="tableColumns" :data="tasks" :bordered="false" :expanded-row-render="expandedRowRender" :row-key="(r:ScraperTask)=>r.id" size="small" />

  <n-empty v-else description="暂无采集任务" size="medium">
    <template #extra>
      <div style="max-width:420px;margin:0 auto;text-align:left">
        <div v-for="(s,i) in ['在上方输入关键词，选择平台','点击「开始采集」创建任务','完成后可在素材库中查看结果']" :key="i" style="display:flex;align-items:center;gap:10px;padding:8px 0;color:#555;font-size:14px">
          <span style="width:24px;height:24px;border-radius:50%;background:#2080f0;color:#fff;font-size:12px;font-weight:bold;display:flex;align-items:center;justify-content:center;flex-shrink:0">{{ i+1 }}</span>
          <span>{{ s }}</span>
        </div>
        <div style="margin-top:16px;padding:10px;background:#f0f9eb;border-radius:6px;color:#666;font-size:12px">💡 提示：小红书和抖音反爬严格，推荐使用<b>浏览器插件</b>一键抓取。</div>
      </div>
    </template>
  </n-empty>

  <!-- 日志查看器 -->
  <div v-if="logTaskId!==null" class="log-viewer">
    <div class="log-header">
      <span>📄 任务 #{{ logTaskId }} 日志</span>
      <n-button size="tiny" @click="logTaskId=null;logContent=''">关闭</n-button>
    </div>
    <n-spin :show="logLoading">
      <pre class="log-content">{{ logContent || '（空）' }}</pre>
    </n-spin>
  </div>

  <!-- 漏斗视图弹窗 -->
  <n-modal v-model:show="funnelOpen" preset="card" :title="funnelTaskId!==null ? '📊 任务 #' + funnelTaskId + ' 漏斗视图' : '漏斗视图'" style="max-width:960px">
    <div v-if="funnelTaskId!==null && funnelData" class="funnel-panel-content">
    <!-- 汇总漏斗 -->
    <div class="funnel-section">
      <div class="funnel-section-title">📈 任务汇总</div>
      <div class="funnel-bars">
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">搜索提取</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelData.summary.total_found,funnelData.summary.total_found),background:barColor(0)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelData.summary.total_found }}</span>
          <span class="funnel-bar-pct">100%</span>
        </div>
        <div class="funnel-drop">↓ -{{ funnelData.summary.skipped_url_seen }} 已存在</div>
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">去重后</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelData.summary.total_found - funnelData.summary.skipped_url_seen, funnelData.summary.total_found),background:barColor(1)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelData.summary.total_found - funnelData.summary.skipped_url_seen }}</span>
          <span class="funnel-bar-pct">{{ funnelPct(funnelData.summary.total_found - funnelData.summary.skipped_url_seen, funnelData.summary.total_found) }}</span>
        </div>
        <div class="funnel-drop">↓ -{{ funnelData.summary.skipped_http_error + funnelData.summary.skipped_network_error }} HTTP/网络失败</div>
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">下载成功</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelData.summary.total_found - funnelData.summary.skipped_url_seen - funnelData.summary.skipped_http_error - funnelData.summary.skipped_network_error, funnelData.summary.total_found),background:barColor(2)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelData.summary.total_found - funnelData.summary.skipped_url_seen - funnelData.summary.skipped_http_error - funnelData.summary.skipped_network_error }}</span>
          <span class="funnel-bar-pct">{{ funnelPct(funnelData.summary.total_found - funnelData.summary.skipped_url_seen - funnelData.summary.skipped_http_error - funnelData.summary.skipped_network_error, funnelData.summary.total_found) }}</span>
        </div>
        <div class="funnel-drop">↓ -{{ funnelData.summary.total_found - funnelData.summary.skipped_url_seen - funnelData.summary.skipped_http_error - funnelData.summary.skipped_network_error - funnelData.summary.total_added }} 内容MD5重复</div>
        <div class="funnel-bar-row funnel-bar-final">
          <span class="funnel-bar-label">★ 最终入库</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelData.summary.total_added, funnelData.summary.total_found),background:barColor(3)}"></div>
          </div>
          <span class="funnel-bar-count" style="font-weight:700">{{ funnelData.summary.total_added }}</span>
          <span class="funnel-bar-pct" style="font-weight:700">{{ funnelPct(funnelData.summary.total_added, funnelData.summary.total_found) }}</span>
        </div>
      </div>
    </div>

    <!-- 每次搜索明细 -->
    <div v-if="funnelData.per_search.length" class="funnel-section">
      <div class="funnel-section-title">🔍 每次搜索明细（{{ funnelData.per_search.length }} 次）</div>
      <div v-for="(ps, psi) in funnelData.per_search" :key="psi" class="funnel-per-search">
        <div class="funnel-ps-header">
          <n-tag size="tiny" :bordered="false">#{{ psi + 1 }}</n-tag>
          <strong>{{ ps.keyword }}</strong>
          <span class="funnel-ps-sort">{{ sortLabel(ps.sort_type) }}</span>
          <n-tag v-if="ps.error" type="error" size="tiny">{{ ps.error }}</n-tag>
        </div>
        <div v-if="!ps.error" class="funnel-bars funnel-bars-sm">
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">卡片</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.cards_total!,ps.cards_total!),background:barColor(0)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.cards_total }}</span>
          </div>
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">有图片</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.cards_with_img!,ps.cards_total!),background:barColor(1)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.cards_with_img }}</span>
            <span class="funnel-bar-pct" v-if="ps.cards_total">(-{{ ps.cards_total! - ps.cards_with_img! }} 无图)</span>
          </div>
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">有效URL</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.urls_extracted!,ps.cards_total!),background:barColor(2)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.urls_extracted }}</span>
            <span class="funnel-bar-pct" v-if="ps.cards_total && ps.cards_with_img">({{ ps.skipped_small && ps.skipped_icon ? '-小图'+(ps.skipped_small||0)+'/图标'+(ps.skipped_icon||0) : '' }})</span>
          </div>
          <div class="funnel-bar-row funnel-bar-final">
            <span class="funnel-bar-label">入库</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.batch_added!,ps.cards_total!),background:barColor(3)}"></div>
            </div>
            <span class="funnel-bar-count" style="font-weight:700">{{ ps.batch_added }}</span>
            <span class="funnel-bar-pct" v-if="ps.batch_skipped_existing||ps.batch_skipped_http||ps.batch_skipped_network">(跳过: 已存在{{ps.batch_skipped_existing}} HTTP{{ps.batch_skipped_http}} 网络{{ps.batch_skipped_network}})</span>
          </div>
        </div>
      </div>
    </div>
    </div>
  </n-modal>

  <!-- 结果预览 -->
  <div v-if="resultsTaskId!==null" class="results-panel">
    <n-spin :show="resultsLoading">
      <div class="results-header">
        <span>📋 结果（共 {{ resultsTotal }} 张）</span>
        <n-space>
          <n-button size="tiny" @click="selectAll">{{ selectedIds.size===resultsItems.length?'取消全选':'全选' }}</n-button>
          <n-popconfirm v-if="selectedIds.size>0" @positive-click="deleteSelected">
            <template #trigger><n-button size="tiny" type="error" ghost :loading="deletingResults">删除 ({{ selectedIds.size }})</n-button></template>
            确定删除 {{ selectedIds.size }} 个素材？
          </n-popconfirm>
        </n-space>
      </div>
      <div v-if="resultsItems.length===0&&!resultsLoading" class="results-empty">空空如也</div>
      <div v-else class="results-grid">
        <div v-for="item in resultsItems" :key="item.id" class="result-card" :class="{selected:selectedIds.has(item.id)}" @click="toggleSelect(item.id)">
          <img :src="getFileUrl(item.thumbnail_path||item.file_path)" loading="lazy" />
          <div class="result-check"><n-checkbox :checked="selectedIds.has(item.id)" size="small" /></div>
        </div>
      </div>
    </n-spin>
  </div>
</n-card>
</div>
</template>

<style scoped>
.scraper-page{max-width:940px;margin:0 auto}
.subtitle{color:#999;margin-bottom:16px}

.cookie-cards{display:flex;gap:12px;margin-bottom:16px}

.tombstone-header{display:flex;justify-content:space-between;align-items:center;font-size:14px}
.tombstone-body{border-top:1px solid #eee;margin-top:8px}

.log-viewer{margin-top:12px;border:1px solid #333;border-radius:8px;overflow:hidden}
.log-header{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:#333;color:#0f0;font-size:13px}
.log-content{margin:0;padding:12px;background:#1a1a1a;color:#0f0;font-size:11px;line-height:1.5;max-height:400px;overflow:auto;white-space:pre-wrap;word-break:break-all}

.results-panel{margin-top:16px;border:1px solid #e5e7eb;border-radius:8px;padding:16px;background:#fff}
.results-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;font-size:14px;font-weight:600}
.results-empty{text-align:center;color:#999;padding:32px 0;font-size:13px}
.results-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;max-height:500px;overflow-y:auto}
.result-card{position:relative;aspect-ratio:3/4;overflow:hidden;border-radius:6px;border:2px solid transparent;cursor:pointer;transition:border-color .15s;background:#f5f5f5}
.result-card.selected{border-color:#2080f0}
.result-card img{width:100%;height:100%;object-fit:cover}
.result-check{position:absolute;top:4px;right:4px}
.chrome-cmd{display:block;background:#f0f0f0;padding:4px 8px;margin:4px 0;border-radius:4px;font-size:11px;cursor:pointer;user-select:all}

/* 漏斗视图 */
.funnel-panel-content{max-height:72vh;overflow-y:auto;padding-right:4px}
.funnel-section{margin-bottom:20px}
.funnel-section-title{font-size:13px;font-weight:600;color:#333;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #f0f0f0}
.funnel-bars{display:flex;flex-direction:column;gap:4px}
.funnel-bars-sm{gap:2px}
.funnel-bar-row{display:flex;align-items:center;gap:8px;font-size:12px}
.funnel-bar-label{width:56px;flex-shrink:0;text-align:right;color:#666;font-size:11px}
.funnel-bar-track{flex:1;height:18px;background:#f5f5f5;border-radius:4px;overflow:hidden;min-width:60px}
.funnel-bar-fill{height:100%;border-radius:4px;transition:width .4s ease;min-width:2px;opacity:.85}
.funnel-bar-count{width:36px;flex-shrink:0;text-align:right;font-variant-numeric:tabular-nums;font-size:12px;color:#333}
.funnel-bar-pct{width:44px;flex-shrink:0;font-size:10px;color:#999;font-variant-numeric:tabular-nums}
.funnel-bar-final{border-top:1px dashed #e0e0e0;padding-top:4px;margin-top:2px}
.funnel-drop{font-size:10px;color:#999;padding-left:64px}
.funnel-per-search{margin-bottom:12px;padding:10px;background:#fafafa;border-radius:6px}
.funnel-ps-header{display:flex;align-items:center;gap:6px;margin-bottom:8px;font-size:13px}
.funnel-ps-sort{font-size:11px;color:#999}
</style>
