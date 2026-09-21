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

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { TableColumnData } from '@arco-design/web-vue'
import { useF2Import } from '@/composables/useF2Import'
import { authorColumnSorter } from '@/utils/f2Authors'
import { describeRunningTask } from '@/utils/taskPresentation'

const router = useRouter()

const emit = defineEmits<{ (e: 'submitted'): void }>()

const {
  status,
  statusLoading,
  submitting,
  loadStatus,
  submit,
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
const showAdvanced = ref(false)
/** 是否连「未登记到博主库」的 f2 账号一起处理（默认否：f2 用户库混进的无关账号不入库） */
const includeUnknown = ref(false)
/** 后端识别出的「f2 里有、但库里没有对应博主」的账号名（默认会被跳过） */
const unknownAuthors = computed(() => status.value?.unknown_authors ?? [])
/** f2 日期窗口（天）：只翻最近 N 天的作品。0=全历史（很慢，见下方说明）。
 *  初值取后端配置（F2_FETCH_SINCE_DAYS），不在前端硬编码——否则改 .env 不生效：
 *  前端总把写死的默认值随请求下发，会把后端配置顶掉。留空表示「用后端配置」。 */
const sinceDays = ref<number | undefined>(undefined)

// 只在首次拿到状态时填充默认窗口；之后轮询不回写，用户改过的值不被覆盖
let sinceDaysFilled = false
watch(
  () => status.value?.fetch_since_days,
  (value) => {
    if (sinceDaysFilled || value == null) return
    sinceDays.value = value
    sinceDaysFilled = true
  },
  { immediate: true },
)

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
      // fetch_mode 决定说的是哪个入口，like_progress 提供「已下载 N 个文件」的实时证据
      fetch_mode: task.fetch_mode,
      like_progress: task.like_progress,
    },
    task.status,
    task.done,
    task.total,
  )
  return `正在执行任务 #${task.id}（${task.progress}%）${detail ? ` · ${detail}` : ''}`
})

let timer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

watch(
  () => status.value?.auto?.running_task_id ?? null,
  (taskId) => {
    stopPolling()
    if (taskId === null) return
    // silent：轮询不改 statusLoading，否则状态行每 5 秒闪一次加载态
    timer = setInterval(() => void loadStatus({ silent: true }), 5000)
  },
)

onMounted(loadStatus)
onUnmounted(stopPolling)

async function onSubmit() {
  const taskId = await submit({
    fetch: fetchFirst.value,
    authors: authorsText.value.trim() || undefined,
    limit: limit.value || undefined,
    skip_live: skipLive.value || undefined,
    make_thumbnails: makeThumbnails.value,
    since_days: sinceDays.value,
    include_unknown_authors: includeUnknown.value || undefined,
  })
  if (taskId) {
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
 */
const profileText = ref('')

/** 逗号/空白分隔 → 数组；过滤空项 */
const profileList = computed(() =>
  profileText.value
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean),
)
const canSubmitProfile = computed(() => profileList.value.length > 0)

async function onSubmitProfile() {
  const taskId = await submit({
    fetch: true,
    profiles: profileList.value,
    // 点名博主只下她，不套「已登记博主」白名单（后端已保证），也不带其他筛选条件
    make_thumbnails: makeThumbnails.value,
  })
  if (taskId) {
    profileText.value = ''
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
      <a-button
        type="primary"
        :loading="submitting"
        :disabled="!status?.available && !includeUnknown"
        @click="onSubmit"
      >
        {{ fetchFirst ? '一键获取素材' : '仅入库已下载' }}
      </a-button>
    </div>

    <!-- 按博主全量下载：从「我的喜欢」里发现新博主时的入口（f2 用户库里没有也能下） -->
    <div class="f2-profile">
      <div class="f2-profile-title">按博主全量下载</div>
      <div class="f2-profile-row">
        <a-input
          v-model="profileText"
          placeholder="博主主页链接 或 sec_user_id（如 https://www.douyin.com/user/MS4wLjABAAAA…，多个用逗号分隔）"
          allow-clear
        />
        <a-button
          type="outline"
          :loading="submitting"
          :disabled="!canSubmitProfile"
          @click="onSubmitProfile"
        >
          下载她全部作品
        </a-button>
      </div>
      <div class="f2-profile-tip">
        从「我的喜欢」里发现一个没订阅过的博主？用这里。f2 只认它自己见过的账号，
        所以「一键获取素材」下不了新博主（会得到 0 个作者）；这个入口直接按主页链接
        点名，**首次自动翻全量**（不是只拿最近几天），下完自动入库。
        <br />
        ⚠ 抖音号（如 72906514384）与 v.douyin.com 短链不支持——请填完整主页链接里的
        <code>user/</code> 后面那段。
      </div>
    </div>

    <!-- 可用性状态：不可用时说明原因（f2 未装 / 目录缺失 / 用户库为空 / 对不上博主） -->
    <a-spin :loading="statusLoading" style="display: block">
      <div v-if="status" class="f2-status" :class="{ 'is-bad': !status.available }">
        <span>{{ status.available ? '✅' : '⚠️' }} {{ status.reason }}</span>
        <a-link @click="loadStatus()">刷新状态</a-link>
      </div>
    </a-spin>

    <!-- 已登记博主清单：白名单里有谁、各自已入库多少条素材（展开时才拉取） -->
    <div class="f2-authors-bar">
      <a-link @click="toggleAuthors">
        {{ showAuthors ? '收起已登记博主' : `查看已登记博主（${status?.authors ?? 0}）` }}
      </a-link>
      <span class="f2-tip">看一下白名单里到底有谁、各自带来多少条素材</span>
      <a-link v-if="showAuthors" @click="loadAuthors()">刷新清单</a-link>
    </div>

    <a-spin v-if="showAuthors" :loading="authorsLoading" style="display: block">
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
            <a-link v-if="record.profile_url" :href="record.profile_url" target="_blank">
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
      <a-link @click="router.push('/tasks')">去任务中心看进度</a-link>
    </div>

    <div class="f2-options">
      <a-checkbox v-model="fetchFirst">先调用 f2 增量下载（只下新作品）</a-checkbox>
      <a-checkbox v-model="makeThumbnails">生成缩略图</a-checkbox>
      <a-checkbox v-model="skipLive">跳过 live 实况分段视频</a-checkbox>
      <a-link @click="showAdvanced = !showAdvanced">
        {{ showAdvanced ? '收起高级选项' : '高级选项' }}
      </a-link>
    </div>

    <div v-if="showAdvanced" class="f2-advanced">
      <a-input
        v-model="authorsText"
        placeholder="只处理指定博主（逗号分隔，如：里香,娜娜瑜；留空=全部）"
        allow-clear
      />
      <a-input-number
        v-model="limit"
        :min="1"
        placeholder="最多导入作品数（留空=不限）"
        style="width: 220px"
      />
      <span class="f2-tip">只翻最近</span>
      <a-input-number
        v-model="sinceDays"
        :min="0"
        :max="3650"
        size="small"
        style="width: 100px"
        placeholder="用配置"
      />
      <span class="f2-tip">
        天的作品（默认取后端配置 {{ status?.fetch_since_days ?? '—' }} 天；0=翻全历史）。f2
        翻页固定每页等 10 秒， 窗口越小越快；按作者上次下载时间会自动放大，长时间不跑也不漏。
      </span>
      <span class="f2-tip">
        下载目录：{{ status?.root || '—' }}；已登记博主来自 f2 用户库，共
        {{ status?.authors ?? 0 }} 个
      </span>
      <a-checkbox v-model="includeUnknown">包含 f2 里未登记到博主库的账号</a-checkbox>
      <span class="f2-tip">
        {{
          unknownAuthors.length
            ? `默认跳过这 ${unknownAuthors.length} 个账号：${unknownAuthors.join('、')}`
            : 'f2 用户库里没有多出来的账号'
        }}。f2 用户库存的是它见过的所有账号，勾选后这些账号的作品也会被下载并入库。
      </span>
    </div>
  </a-card>
</template>

<style scoped>
.f2-card {
  margin-bottom: 16px;
  background: linear-gradient(180deg, #f5f7ff 0%, #ffffff 100%);
  border: 1px solid #e5e9f5;
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
  color: #1d2129;
}

.f2-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #86909c;
  line-height: 1.6;
}

.f2-hint {
  color: #d97706;
}

.f2-status {
  margin-top: 10px;
  font-size: 13px;
  color: #4b5563;
  display: flex;
  align-items: center;
  gap: 12px;
}

.f2-status.is-bad {
  color: #d97706;
}

.f2-running {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: 4px;
  background: #f2f6ff;
  font-size: 13px;
  color: #1d2129;
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
  color: #9ca3af;
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
  color: #8a8f99;
}
/* 库内博主名与 f2 昵称一致时弱化，避免两列看起来重复 */
.f2-dim {
  color: #9ca3af;
}

/* 「按博主全量下载」区块：与上方的增量入口用左侧竖线区分开 */
.f2-profile {
  margin-top: 12px;
  padding: 10px 12px;
  border-left: 3px solid var(--color-primary-light-3, #94bfff);
  background: #f8faff;
  border-radius: 4px;
}
.f2-profile-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d2129;
  margin-bottom: 8px;
}
.f2-profile-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.f2-profile-row :deep(.arco-input-wrapper) {
  flex: 1;
  min-width: 260px;
}
.f2-profile-tip {
  margin-top: 8px;
  font-size: 12px;
  color: #8a8f99;
  line-height: 1.8;
}
.f2-profile-tip code {
  padding: 0 3px;
  border-radius: 3px;
  background: #f2f3f5;
}
</style>
