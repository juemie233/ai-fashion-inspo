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
import { useF2Import } from '@/composables/useF2Import'
import { describeRunningTask } from '@/utils/taskPresentation'

const router = useRouter()

const emit = defineEmits<{ (e: 'submitted'): void }>()

const { status, statusLoading, submitting, loadStatus, submit } = useF2Import()

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
    { stage: task.stage },
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

    <!-- 可用性状态：不可用时说明原因（f2 未装 / 目录缺失 / 用户库为空 / 对不上博主） -->
    <a-spin :loading="statusLoading" style="display: block">
      <div v-if="status" class="f2-status" :class="{ 'is-bad': !status.available }">
        <span>{{ status.available ? '✅' : '⚠️' }} {{ status.reason }}</span>
        <a-link @click="loadStatus()">刷新状态</a-link>
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
</style>
