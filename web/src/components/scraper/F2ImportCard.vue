<script setup lang="ts">
/** 抖音「一键获取素材」（f2）卡片：增量下载 → 去重 → 入库。
 *
 * 与 CLI（`python -m scripts.import_f2_downloads --fetch --apply`）同一条链路，
 * 这里只是把入口搬到界面上：提交后走后台任务队列，进度在「任务中心」看。
 *
 * 两条约定（与后端一致，界面上明确告知用户）：
 *  - 导入**不做标签分析**：素材以未打标状态入库，打标请用「批量分析任务」
 *  - 入库前**五层去重**：同一内容不会重复入库，重复点击也安全
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Message, type TableColumnData } from '@arco-design/web-vue'
import { IconCheckCircleFill, IconExclamationCircleFill } from '@arco-design/web-vue/es/icon'
import { useF2Import, type F2ImportOptions } from '@/composables/useF2Import'
import type { F2PreviewResult } from '@/types/scraper'
import { usePolling } from '@/composables/usePolling'
import { useDouyinBloggerPicker } from '@/composables/useDouyinBloggerPicker'
import { mergeProfileKeys } from '@/utils/bloggerWorks'
import { authorColumnSorter } from '@/utils/f2Authors'
import { describeRunningTask } from '@/utils/taskPresentation'

const router = useRouter()

const emit = defineEmits<{ (e: 'submitted'): void }>()

const {
  status,
  statusLoading,
  submitting,
  previewing,
  loadStatus,
  submit,
  preview,
  f2Authors,
  authorsLoading,
  loadAuthors,
} = useF2Import()

// ── 提交选项（默认值即为日常用法：先增量下载，再全量入库）──
const fetchFirst = ref(true)
const authorsText = ref('')
const limit = ref<number | undefined>(undefined)
const skipLive = ref(false)
const makeThumbnails = ref(true)
/** 入库成功后删掉 f2 下载目录里的源文件（默认否：Download/ 只增不减，但删了不可恢复） */
const pruneDownloads = ref(false)
const showAdvanced = ref(false)

// ── 入库前预览（「宁缺毋滥、手动确认」）：先看新增/跳过明细，再决定是否入库 ──
const previewVisible = ref(false)
const previewResult = ref<F2PreviewResult | null>(null)
/** 预览时的参数快照：确认入库必须提交它，而不是重新读当前表单 */
const previewSnapshot = ref<F2ImportOptions | null>(null)
function currentOptions(): F2ImportOptions {
  return {
    fetch: fetchFirst.value,
    authors: authorsText.value.trim() || undefined,
    limit: limit.value || undefined,
    skip_live: skipLive.value || undefined,
    make_thumbnails: makeThumbnails.value,
    prune_downloads: pruneDownloads.value,
    since_days: sinceDays.value,
    include_unknown_authors: includeUnknown.value || undefined,
  }
}

async function onPreview() {
  const opts = currentOptions()
  const data = await preview(opts)
  if (!data) return
  previewSnapshot.value = opts
  previewResult.value = data
  previewVisible.value = true
}
/** 是否连「未登记到博主库」的 f2 账号一起处理（默认否：f2 用户库混进的无关账号不入库） */
const includeUnknown = ref(false)
/** 后端识别出的「f2 里有、但库里没有对应博主」的账号名（默认会被跳过） */
const unknownAuthors = computed(() => status.value?.unknown_authors ?? [])
/** f2 日期窗口（天）：只翻最近 N 天的作品。0=全历史（很慢，见下方说明）。
 *  初值取后端配置（F2_FETCH_SINCE_DAYS），不在前端硬编码——否则改 .env 不生效：
 *  前端总把写死的默认值随请求下发，会把后端配置顶掉。留空表示「用后端配置」。 */
const sinceDays = ref<number | undefined>(undefined)

// ── 进行中任务的实时说明：让「1% 挂好几分钟」有解释 ──
// 只在确实有任务在跑时轮询（5 秒一次），没有任务时立即停，避免无意义请求。
const running = computed(() => status.value?.auto?.running ?? null)
const runningText = computed(() => {
  const task = running.value
  if (!task) return ''
  const detail = describeRunningTask(
    'f2_import',
    {
      stage: task.stage,
      // 「我的喜欢」与博主主页共用 f2_import 类型，文案口径靠这两个字段区分：
      // fetch_mode 决定说的是哪个入口，download_progress 提供「已下载 N 个文件」的实时证据
      fetch_mode: task.fetch_mode,
      download_progress: task.download_progress,
    },
    task.status,
    task.done,
    task.total,
  )
  return `正在执行任务 #${task.id}（${task.progress}%）${detail ? ` · ${detail}` : ''}`
})

// 进行中任务的状态轮询：定时器生命周期（含卸载自动停止）交给 usePolling，
// 不再在本组件里手写 setInterval —— 项目里已有 8 处用它，这里是唯一自己实现的一份。
const { start: startStatusPolling, stop: stopStatusPolling } = usePolling({
  intervalMs: 5000,
  // silent：轮询不改 statusLoading，否则状态行每 5 秒闪一次加载态
  callback: () => void loadStatus({ silent: true }),
  immediate: false,
})

watch(
  () => status.value?.auto?.running_task_id ?? null,
  async (taskId, previousTaskId) => {
    stopStatusPolling()
    if (taskId !== null) {
      startStatusPolling()
      return
    }
    // 从「有任务」变为「无任务」：提示结束、刷新清单并让父级重载历史
    if (previousTaskId !== null && previousTaskId !== undefined) {
      Message.success('f2 导入任务已结束，正在刷新状态')
      if (showAuthors.value) await loadAuthors()
      emit('submitted')
    }
  },
)

async function onSubmit(options: F2ImportOptions | undefined = undefined) {
  const taskId = await submit(options ?? currentOptions())
  if (taskId) {
    previewVisible.value = false
    previewSnapshot.value = null
    emit('submitted')
    await loadStatus()
  }
}

/**
 * 「按博主全量下载」：给一个博主，下她全部作品。
 *
 * 为什么需要独立入口：f2 的下载目标**只来自它自己的用户库**，库里没有的账号
 * 跑不到——从「我的喜欢」里发现一个新博主时，走「一键获取素材」会得到
 * 「下载 0 个作者 + 入库 0」。这个入口用主页链接直接点名，不依赖 f2 用户库。
 *
 * 点名方式两种，可混用：① 上面的选择器从**已登记抖音博主**里挑（名字/主页链接现成）；
 * ② 手粘主页链接（库外的博主、或只想试一个人的时候）。
 */
const profileText = ref('')

/** 手填的标识（中英文逗号/顿号/空白分隔 → 数组；过滤空项） */
const manualProfileList = computed(() =>
  profileText.value
    .split(/[,，、\s]+/)
    .map((s) => s.trim())
    .filter(Boolean),
)

/** 合法抖音主页链接或 sec_user_id；短链/抖音号/分享文案一律判为非法。 */
const DOUYIN_PROFILE_RE = /^https?:\/\/(?:www\.)?douyin\.com\/user\/[A-Za-z0-9_-]{5,}\/?$/i
const SEC_USER_ID_RE = /^MS4wLj[A-Za-z0-9_-]{10,}$/
function isValidProfileKey(value: string): boolean {
  return DOUYIN_PROFILE_RE.test(value) || SEC_USER_ID_RE.test(value)
}
const invalidManualProfiles = computed(() =>
  manualProfileList.value.filter((item) => !isValidProfileKey(item)),
)
const validManualProfiles = computed(() => manualProfileList.value.filter(isValidProfileKey))
const safeProfileUrl = (value: string): string | undefined => {
  if (!/^https?:\/\//i.test(value)) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? value : undefined
  } catch {
    return undefined
  }
}

// 博主选择器：打开卡片就取一次「素材数最多的前 N 位抖音博主」，之后按关键字远程搜索
const {
  selectOptions: pickerOptions,
  loading: pickerLoading,
  selectedIds: pickerSelectedIds,
  selected: pickerSelected,
  selectedKeys: pickerSelectedKeys,
  missing: pickerMissing,
  resolving: pickerResolving,
  progress: pickerProgress,
  search: searchBloggers,
  onChange: onPickerChange,
  resolveMissing: resolveMissingProfiles,
  clear: clearPicker,
} = useDouyinBloggerPicker()

// 状态与候选一起拿：f2 **明确不可用**时选择器点不动，就不必再拉一次博主清单
// （状态还没回来 / 后端版本较旧时 `undefined`，照常拉）
onMounted(async () => {
  await loadStatus()
  if (status.value?.profiles_available !== false) void searchBloggers()
})

/** 最终提交的标识：选择器选中的博主 + 手填链接，去重 */
const profileList = computed(() =>
  mergeProfileKeys(pickerSelectedKeys.value, validManualProfiles.value),
)
const canSubmitProfile = computed(
  () => profileList.value.length > 0 && invalidManualProfiles.value.length === 0,
)
const profileConfirmVisible = ref(false)
const profileConfirmNames = computed(() => {
  const selectedNames = pickerSelected.value.map((p) => p.name)
  return [...selectedNames, ...validManualProfiles.value]
})

/** 打开确认框；真正提交在 confirmProfileSubmit。 */
function askSubmitProfile() {
  if (!canSubmitProfile.value) return
  profileConfirmVisible.value = true
}

async function confirmProfileSubmit() {
  const taskId = await submit({
    fetch: true,
    profiles: profileList.value,
    // 点名博主不套「已登记博主」白名单；「跳过 live」与「清理源文件」只在
    // 增量入库链路生效，这里不传，界面上明确标注不适用。
    make_thumbnails: makeThumbnails.value,
  })
  if (taskId) {
    profileConfirmVisible.value = false
    profileText.value = ''
    clearPicker()
    emit('submitted')
    await loadStatus()
  }
}

// ── 已登记博主清单：白名单里到底有谁、各自已入库多少条素材 ──
/** 是否展开清单（展开时才拉数据，避免每次进页面都多一次请求） */
const showAuthors = ref(false)

/** 清单表格列（昵称链到抖音主页，便于核对是不是同一个人）
 *
 * 「总作品 / 已入库素材」两列可点表头排序：首次点击降序（多的在前），再点升序，
 * 第三次取消排序（Arco `sortDirections` 的固定循环）。
 */
const authorColumns: TableColumnData[] = [
  { title: '博主（f2 昵称）', dataIndex: 'nickname', slotName: 'nickname', width: 200 },
  { title: '库内博主', dataIndex: 'bloggerName', slotName: 'bloggerName', width: 140 },
  {
    title: '总作品',
    dataIndex: 'aweme_count',
    slotName: 'awemeCount',
    width: 88,
    sortable: {
      sortDirections: ['descend', 'ascend'],
      sorter: authorColumnSorter('aweme_count'),
    },
  },
  {
    title: '已入库素材',
    dataIndex: 'materials',
    slotName: 'materials',
    width: 104,
    sortable: {
      sortDirections: ['descend', 'ascend'],
      sorter: authorColumnSorter('materials'),
    },
  },
]

async function toggleAuthors() {
  showAuthors.value = !showAuthors.value
  // 展开时若已有数据就不再重复请求；需要最新数据用「刷新状态」或清单上的刷新按钮
  if (showAuthors.value && !f2Authors.value) await loadAuthors()
}
</script>

<template>
  <a-card class="f2-card" :bordered="false">
    <div class="f2-head">
      <div>
        <div class="f2-title">一键获取素材（抖音 · f2）</div>
        <div class="f2-sub">
          调 f2 增量下载已采集博主的新作品 → 自动去重 → 入素材库。
          <span class="f2-hint"
            >导入不做标签分析（素材为未打标状态），可稍后用批量分析任务补。</span
          >
        </div>
      </div>
      <a-space>
        <!-- 先预览再确认：CLI 缺省就是这个行为（只出计划、不入库） -->
        <a-button :loading="previewing" :disabled="submitting" @click="onPreview">预览</a-button>
        <a-button
          type="primary"
          :loading="submitting"
          :disabled="previewing || (fetchFirst && !status?.available && !includeUnknown)"
          @click="onSubmit()"
        >
          {{ fetchFirst ? '一键获取素材' : '仅入库已下载' }}
        </a-button>
      </a-space>
    </div>

    <!-- 按博主全量下载：从「我的喜欢」里发现新博主时的入口（f2 用户库里没有也能下） -->
    <div class="f2-profile">
      <div class="f2-profile-title">按博主全量下载</div>
      <div class="f2-profile-row">
        <!-- 选择器：直接从已登记抖音博主里挑（可搜索、可多选） -->
        <a-select
          v-model="pickerSelectedIds"
          :options="pickerOptions"
          :loading="pickerLoading"
          :filter-option="false"
          multiple
          allow-search
          allow-clear
          placeholder="搜索并选择已登记抖音博主（可多选）"
          class="f2-profile-picker"
          @search="searchBloggers"
          @change="onPickerChange"
        />
        <a-button
          type="outline"
          :loading="submitting"
          :disabled="!canSubmitProfile || invalidManualProfiles.length > 0"
          @click="askSubmitProfile"
        >
          下载该博主全部作品
        </a-button>
      </div>
      <div class="f2-profile-row f2-profile-row--spaced">
        <a-input
          v-model="profileText"
          placeholder="粘贴主页链接 / sec_user_id（中英文逗号、顿号或空格分隔）"
          allow-clear
          :disabled="profileConfirmVisible"
        />
      </div>
      <div v-if="invalidManualProfiles.length" class="f2-profile-warn">
        以下内容不是受支持的博主标识，请改成 douyin.com/user/… 链接或 sec_user_id：
        {{ invalidManualProfiles.join('、') }}
      </div>
      <div class="f2-profile-options-note">
        此入口只使用「生成缩略图」；「跳过 live」「清理下载源文件」只在增量入库入口生效。
      </div>
      <!-- 选中的博主缺主页标识：先解析再提交（否则后端点名不到她） -->
      <div v-if="pickerMissing.length" class="f2-profile-warn">
        <span
          >{{
            pickerMissing.map((p) => p.name).join('、')
          }}
          还没有主页链接/sec_user_id，提交时会被跳过：</span
        >
        <a-button
          type="text"
          size="mini"
          :loading="pickerResolving"
          @click="resolveMissingProfiles()"
        >
          {{
            pickerResolving && pickerProgress.total
              ? `解析中 ${pickerProgress.done}/${pickerProgress.total}…`
              : '自动解析主页'
          }}
        </a-button>
        <span class="f2-tip">（用其素材作品 ID 反查，约 20~40 秒/人）</span>
      </div>
      <div class="f2-profile-tip">
        从「我的喜欢」里发现一个没订阅过的博主？用这里。f2 只认它自己见过的账号，
        所以「一键获取素材」下不了新博主（会得到 0 个作者）；这个入口直接点名，
        <b>首次自动翻全量</b>（不是只拿最近几天），下完自动入库。
        <br />
        抖音号（如 72906514384）与 v.douyin.com 短链不支持——手填时请用完整主页链接里的
        <code>user/</code> 后面那段；从上面选择器挑的博主不受此限。
      </div>
    </div>

    <!-- 按博主全量下载确认：全量翻页很慢，先明确要下谁、耗时与磁盘成本 -->
    <a-modal
      v-model:visible="profileConfirmVisible"
      title="确认按博主全量下载"
      :footer="false"
      :width="520"
      unmount-on-close
    >
      <div class="f2-profile-confirm">
        <p>将为以下 {{ profileConfirmNames.length }} 位博主首次翻全量作品：</p>
        <ul>
          <li v-for="name in profileConfirmNames" :key="name">{{ name }}</li>
        </ul>
        <p class="f2-confirm-warn">
          全量翻页固定每页等待，作者作品多时可能持续数十分钟到数小时；会占用下载目录磁盘空间。
        </p>
      </div>
      <div class="f2-preview-actions">
        <a-button @click="profileConfirmVisible = false">取消</a-button>
        <a-button type="primary" :loading="submitting" @click="confirmProfileSubmit">
          确认下载
        </a-button>
      </div>
    </a-modal>

    <!-- 可用性状态：不可用时说明原因（f2 未装 / 目录缺失 / 用户库为空 / 对不上博主） -->
    <a-spin :loading="statusLoading" class="f2-status-spin">
      <div v-if="status" class="f2-status" :class="status.available ? 'is-ok' : 'is-bad'">
        <IconCheckCircleFill v-if="status.available" class="f2-status-icon" />
        <IconExclamationCircleFill v-else class="f2-status-icon" />
        <span>{{ status.reason }}</span>
        <a-button type="text" size="mini" @click="loadStatus()">刷新状态</a-button>
      </div>
    </a-spin>

    <!-- 已登记博主清单：白名单里有谁、各自已入库多少条素材（展开时才拉取） -->
    <div class="f2-authors-bar">
      <a-button type="text" size="mini" @click="toggleAuthors">
        {{ showAuthors ? '收起已登记博主' : `查看已登记博主（${status?.authors ?? 0}）` }}
      </a-button>
      <span class="f2-tip">看一下白名单里到底有谁、各自带来多少条素材</span>
      <a-button v-if="showAuthors" type="text" size="mini" @click="loadAuthors()">
        刷新清单
      </a-button>
    </div>

    <a-spin v-if="showAuthors" :loading="authorsLoading" class="f2-status-spin">
      <div v-if="f2Authors" class="f2-authors">
        <a-table
          v-if="f2Authors.registered.length"
          :columns="authorColumns"
          :data="f2Authors.registered"
          :pagination="false"
          row-key="sec_user_id"
          size="small"
          :scroll="{ x: 540 }"
        >
          <template #nickname="{ record }">
            <a-link
              v-if="safeProfileUrl(record.profile_url)"
              :href="safeProfileUrl(record.profile_url)"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ record.nickname || `${record.sec_user_id.slice(0, 12)}…` }}
            </a-link>
            <span v-else>{{ record.nickname || '（无昵称）' }}</span>
          </template>
          <template #bloggerName="{ record }">
            <span :class="{ 'f2-dim': record.blogger_name === record.nickname }">
              {{ record.blogger_name || '—' }}
            </span>
          </template>
          <template #awemeCount="{ record }">
            {{ record.aweme_count || '—' }}
          </template>
          <template #materials="{ record }">{{ record.materials }}</template>
        </a-table>
        <div v-else class="f2-tip">白名单里暂无账号——先补全博主的 sec_user_id 或跑一次 f2</div>
        <div class="f2-authors-note">{{ f2Authors.note }}</div>
      </div>
    </a-spin>

    <!-- 正在跑的任务：说明当前阶段与计数（进度条百分比长期不动时也心里有数） -->
    <div v-if="runningText" class="f2-running">
      <a-spin :size="14" />
      <span>{{ runningText }}</span>
      <a-button type="text" size="mini" @click="router.push('/tasks')"> 去任务中心看进度 </a-button>
    </div>

    <div class="f2-options">
      <a-checkbox v-model="fetchFirst" :disabled="previewing">
        先调用 f2 增量下载（只下新作品）
      </a-checkbox>
      <a-checkbox v-model="makeThumbnails" :disabled="previewing">生成缩略图</a-checkbox>
      <a-checkbox v-model="skipLive" :disabled="previewing">跳过 live 实况分段视频</a-checkbox>
      <a-checkbox v-model="pruneDownloads" :disabled="previewing">
        入库后清理下载目录源文件
      </a-checkbox>
      <a-button type="text" size="mini" @click="showAdvanced = !showAdvanced">
        {{ showAdvanced ? '收起高级选项' : '高级选项' }}
      </a-button>
    </div>
    <div v-if="pruneDownloads" class="f2-danger-note">
      会删除下载目录里的源文件，删除后不可恢复；仅对本次成功入库的文件生效。
    </div>

    <div v-if="showAdvanced" class="f2-advanced">
      <a-input
        v-model="authorsText"
        placeholder="只处理指定博主（逗号分隔，如：里香,娜娜瑜；留空=全部）"
        allow-clear
        :disabled="previewing"
      />
      <a-input-number
        v-model="limit"
        :min="1"
        placeholder="最多导入作品数（留空=不限）"
        class="f2-input-limit"
        :disabled="previewing"
      />
      <span class="f2-tip">只翻最近</span>
      <a-input-number
        v-model="sinceDays"
        :min="0"
        :max="3650"
        size="small"
        class="f2-input-since"
        :placeholder="`用配置（${status?.fetch_since_days ?? '—'} 天）`"
        :disabled="previewing"
      />
      <span class="f2-tip">
        天的作品（留空=后端配置；0=翻全历史）。f2
        翻页固定每页等待，窗口越小越快；按作者上次下载时间会自动放大，长时间不跑也不漏。
      </span>
      <div v-if="sinceDays === 0" class="f2-danger-note">
        0 = 全历史：会从第一页翻到最后一页，可能非常慢；建议先点「预览」确认范围。
      </div>
      <span class="f2-tip">
        下载目录：{{ status?.root || '—' }}；已登记博主来自 f2 用户库，共
        {{ status?.authors ?? 0 }} 个
      </span>
      <a-checkbox v-model="includeUnknown" :disabled="previewing">
        包含 f2 里未登记到博主库的账号
      </a-checkbox>
      <span class="f2-tip">
        {{
          unknownAuthors.length
            ? `默认跳过这 ${unknownAuthors.length} 个账号：${unknownAuthors.join('、')}`
            : 'f2 用户库里没有多出来的账号'
        }}。f2 用户库存的是它见过的所有账号，勾选后这些账号的作品也会被下载并入库。
      </span>
    </div>

    <!-- 入库前预览：新增/跳过明细 + 确认入库（确认时用同一份参数，避免边看边改） -->
    <a-modal
      v-model:visible="previewVisible"
      title="入库前预览"
      :footer="false"
      :width="560"
      unmount-on-close
    >
      <template v-if="previewResult">
        <div v-if="previewSnapshot" class="f2-preview-params">
          <div>
            <b>本次提交参数：</b>
            {{ previewSnapshot.fetch ? '先调用 f2 增量下载' : '仅入库已下载文件' }}
            · {{ previewSnapshot.make_thumbnails ? '生成缩略图' : '不生成缩略图' }}
          </div>
          <div v-if="previewSnapshot.prune_downloads" class="f2-confirm-warn">
            入库后将删除下载目录源文件，不可恢复。
          </div>
          <div v-if="previewSnapshot.include_unknown_authors" class="f2-confirm-warn">
            包含 f2 用户库里未登记到博主库的账号。
          </div>
          <div v-if="previewSnapshot.since_days === 0" class="f2-confirm-warn">
            日期窗口为 0：将翻全历史，耗时可能很长。
          </div>
        </div>
        <div v-if="!previewResult.has_any" class="f2-preview-empty">
          下载目录里没有可识别的 f2 产物（{{ previewResult.root }}）。<br />
          <template v-if="previewSnapshot?.fetch">
            任务会先调用 f2 增量下载，再把新作品入库。
          </template>
          <template v-else>
            请先勾选「先调用 f2 增量下载」再提交，或把已下载文件放进该目录。
          </template>
        </div>
        <template v-else>
          <div class="f2-preview-sum">
            预计新增 <b>{{ previewResult.files_to_import }}</b> 个文件 /
            <b>{{ previewResult.works }}</b> 个作品（扫描到 {{ previewResult.files }} 个文件）
          </div>
          <div v-if="previewResult.deferred_works" class="f2-preview-note">
            另有 {{ previewResult.deferred_works }} 个作品因「最多导入作品数」未处理。
          </div>
          <div v-if="Object.keys(previewResult.skipped).length" class="f2-preview-skip">
            <div class="f2-preview-skip-title">跳过（已在库 / 重复 / 被过滤）：</div>
            <div
              v-for="(count, reason) in previewResult.skipped"
              :key="reason"
              class="f2-preview-skip-row"
            >
              <span>{{ reason }}</span>
              <b>{{ count }}</b>
            </div>
          </div>
          <div class="f2-preview-note">
            预览只统计<b>下载目录里已有</b>的产物；勾选了「先调用 f2
            增量下载」的话，任务会先下新作品， 实际入库可能更多（预览是下限）。
          </div>
        </template>
      </template>
      <div class="f2-preview-actions">
        <a-button @click="previewVisible = false">取消</a-button>
        <a-button
          type="primary"
          :loading="submitting"
          @click="onSubmit(previewSnapshot ?? undefined)"
        >
          确认入库
        </a-button>
      </div>
    </a-modal>
  </a-card>
</template>

<style scoped>
.f2-card {
  margin-bottom: 16px;
  background: linear-gradient(180deg, var(--sheet) 0%, var(--sheet) 100%);
  border: 1px solid var(--line);
}

.f2-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.f2-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.f2-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.6;
}

.f2-hint {
  color: var(--amber);
}

.f2-status {
  margin-top: 10px;
  font-size: 13px;
  color: var(--ink-2);
  display: flex;
  align-items: center;
  gap: 12px;
}

.f2-status-spin {
  display: block;
}

.f2-status.is-ok {
  color: var(--jade);
}

.f2-status.is-bad {
  color: var(--amber);
}

.f2-status-icon {
  flex-shrink: 0;
}

.f2-running {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--pine-soft);
  font-size: 13px;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.f2-options {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.f2-advanced {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.f2-tip {
  font-size: 12px;
  color: var(--ink-3);
}

/* 已登记博主清单：状态行下面一行入口 + 展开后的表格 */
.f2-authors-bar {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.f2-authors {
  margin-top: 8px;
}
.f2-authors-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-3);
}
/* 库内博主名与 f2 昵称一致时弱化，避免两列看起来重复 */
.f2-dim {
  color: var(--ink-3);
}

/* 「按博主全量下载」区块：与上方的增量入口用左侧竖线区分开 */
.f2-profile {
  margin-top: 12px;
  padding: 10px 12px;
  border-left: 3px solid var(--pine);
  background: var(--sheet-2);
  border-radius: var(--radius-sm);
}
.f2-profile-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 8px;
}
.f2-profile-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.f2-profile-row--spaced {
  margin-top: 8px;
}
.f2-profile-row :deep(.arco-input-wrapper) {
  flex: 1;
  min-width: 260px;
}
/* 博主选择器与下方手填框同宽（flex 一份），避免选择器被挤成很窄的一条 */
.f2-profile-picker {
  flex: 1;
  min-width: 320px;
}
.f2-profile-options-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
}
/* 已选博主缺主页标识：橙色提示行（不阻断提交，但要说清谁会掉队） */
.f2-profile-warn {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--amber);
}
.f2-profile-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.8;
}
.f2-profile-tip code {
  padding: 0 3px;
  border-radius: 3px;
  background: var(--sheet-2);
}
.f2-profile-confirm p {
  margin: 0 0 8px;
  color: var(--ink-2);
  line-height: 1.7;
}
.f2-profile-confirm ul {
  margin: 0 0 8px;
  padding-left: 20px;
  max-height: 180px;
  overflow: auto;
}
.f2-profile-confirm li {
  color: var(--ink);
  line-height: 1.8;
}
.f2-confirm-warn {
  margin-top: 8px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  background: var(--seal-soft);
  color: var(--seal);
  font-size: 12px;
  line-height: 1.7;
}
.f2-danger-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--seal);
}
.f2-input-limit {
  width: 220px;
}
.f2-input-since {
  width: 150px;
}

/* ── 入库前预览弹窗 ── */
.f2-preview-params {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--sheet-2);
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.8;
}
.f2-preview-sum {
  font-size: 14px;
  color: var(--ink);
  margin-bottom: 8px;
}
.f2-preview-sum b {
  color: var(--pine);
}
.f2-preview-empty {
  font-size: 13px;
  color: var(--ink-3);
  line-height: 1.9;
}
.f2-preview-note {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.8;
  margin-top: 8px;
}
.f2-preview-skip {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--sheet-2);
}
.f2-preview-skip-title {
  font-size: 12px;
  color: var(--ink-2);
  margin-bottom: 4px;
}
.f2-preview-skip-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.9;
}
.f2-preview-skip-row b {
  color: var(--ink-2);
}
.f2-preview-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>
