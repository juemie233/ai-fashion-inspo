<script setup lang="ts">
/** 抖音「采集我的列表」卡片：点赞（我的喜欢）与收藏（我的收藏）共用。
 *
 * 两种模式同形——f2 拉的都是**登录账号自己**的列表，差别只有 `-M` 取值与产物目录：
 *  - `-M like`（点赞）/ `-M collection`（收藏）：都只有本人可见，所以必须填**你自己**
 *    的主页链接（填一次即写入 .env，之后自动带上，两种模式共用这一个配置）
 *  - **不能靠日期窗口提速**：f2 的点赞/收藏模式根本不读 `-i`（源码实测）。真正能收窄
 *    翻页量的是 `-o/--max-counts`（本卡片的「每次最多翻」），因为 f2 的分页没有
 *    「遇到已下载就停」、每页还要固定等一次 timeout
 *  - 入库完全复用同一条链路：五层判重（内容哈希 / 垃圾桶 / 批次内 / 平台 ID / 参数），
 *    同一作品从主页、喜欢、收藏三条路进来都不会重复；收藏模式还会把本批素材聚合进
 *    「抖音收藏」合集（收藏合计里直接看到数量与体积）；结果审查在「抖音采集历史」里
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useF2Import } from '@/composables/useF2Import'

const props = withDefaults(
  defineProps<{
    /** 我的列表类型：点赞（我的喜欢）/ 收藏（我的收藏） */
    mode?: 'like' | 'collection'
  }>(),
  { mode: 'like' },
)

const emit = defineEmits<{ (e: 'submitted'): void }>()

/** 文案与字段随模式切换（点赞/收藏的实现完全共用，只有说法不同） */
const isCollect = computed(() => props.mode === 'collection')
const copy = computed(() =>
  isCollect.value
    ? {
        title: '采集我的收藏（抖音收藏列表）',
        button: '采集我的收藏',
        unit: '条收藏',
        listName: '收藏列表',
        speedTip:
          'f2 的收藏分页**没有「遇到已下载就停」**（POST + 纯 cookie 翻页），每页还固定等一次 ' +
          'timeout（本机 10 秒），全量时零新增也要空翻几分钟。代价：两次运行之间新增收藏' +
          '超过该条数时会漏，攒了很久没采就填大一点或填 0 全量。**按收藏夹下载时，它是' +
          '「每个夹」的上限**（每个夹各取最近 N 条）。',
        collectTip:
          '入库后本批素材会自动聚合进「抖音收藏」合集（收藏合计里能看到数量与体积），' +
          '不做标签分析（素材为未打标状态）。',
      }
    : {
        title: '采集我的喜欢（抖音点赞）',
        button: '采集我的喜欢',
        unit: '条点赞',
        listName: '点赞列表',
        speedTip:
          'f2 的点赞分页**没有「遇到已下载就停」**，从最新一路翻到底、每页还固定等一次 ' +
          'timeout（本机 10 秒），全量时零新增也要空翻几分钟。代价：两次运行之间新增点赞' +
          '超过该条数时会漏，攒了很久没采就填大一点或填 0 全量。',
        collectTip: '入库不做标签分析（素材为未打标状态）。',
      },
)

const {
  status,
  statusLoading,
  submitting,
  likeUserSaving,
  likeMaxSaving,
  collectFolders,
  collectScanning,
  loadStatus,
  scanCollects,
  submit,
  setLikeUser,
  setLikeMaxCounts,
} = useF2Import()

/** 「我的主页链接 / sec_user_id」输入框：初始值取后端已保存的配置 */
const likeUser = ref('')

// 用户没动过输入框时才回填（避免轮询把正在输入的内容覆盖掉）
// 用 ref 而不是 `let xxx = false`：后者在脚本里只被赋过 false，模板里的
// `likeUserTouched = true` 会被 ts-plugin 按控制流窄化成字面量 false 而报 2322
const likeUserTouched = ref(false)
watch(
  () => status.value?.like_user,
  (value) => {
    if (likeUserTouched.value || value == null) return
    likeUser.value = value
  },
  { immediate: true },
)

/** 「每次最多翻多少条点赞」：0=全量；初始值取后端已保存的配置 */
const likeMaxCounts = ref(0)

const likeMaxTouched = ref(false)
watch(
  () => status.value?.like_max_counts,
  (value) => {
    if (likeMaxTouched.value || value == null) return
    likeMaxCounts.value = value
  },
  { immediate: true },
)

const savedLikeUser = computed(() => status.value?.like_user ?? '')
const savedLikeMax = computed(() => status.value?.like_max_counts ?? 0)
const dirty = computed(() => likeUser.value.trim() !== savedLikeUser.value)
const maxDirty = computed(() => (likeMaxCounts.value || 0) !== savedLikeMax.value)

/** 当前模式的可用性与原因（点赞与收藏的前提相同，但字段分开，便于各自提示） */
const available = computed(() =>
  isCollect.value
    ? Boolean(status.value?.collect_available)
    : Boolean(status.value?.like_available),
)
const availabilityReason = computed(() =>
  isCollect.value ? (status.value?.collect_reason ?? '') : (status.value?.like_reason ?? ''),
)
const canSubmit = computed(() => available.value && Boolean(likeUser.value.trim()))

/** 输入非数字/负数时的兜底：按 0（全量）处理，避免把非法值发给后端 */
const safeMaxCounts = computed(() => {
  const n = Number(likeMaxCounts.value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
})

onMounted(loadStatus)

async function onSaveUser() {
  const ok = await setLikeUser(likeUser.value.trim())
  if (ok) likeUserTouched.value = false
}

async function onSaveMax() {
  const ok = await setLikeMaxCounts(safeMaxCounts.value)
  if (ok) {
    likeMaxTouched.value = false
    likeMaxCounts.value = safeMaxCounts.value
  }
}

/** 采集选项：入库后自动登记来源作者为穿搭博主（默认开） */
const registerBloggers = ref(true)

// ── 「先扫描、后下载」（仅收藏模式）：先列收藏夹，勾掉不要的，再只下勾选的 ──

/** 收藏夹选择弹窗是否打开 */
const folderModalOpen = ref(false)
/** 勾选状态：夹 ID 集合（**扫描后默认全选**，用户取消掉不想要的） */
const checkedFolderIds = ref<string[]>([])
/** 全选/全不选用的派生值 */
const allChecked = computed(
  () => folderList.value.length > 0 && checkedFolderIds.value.length === folderList.value.length,
)
const folderList = computed(() => collectFolders.value?.folders ?? [])
/** 勾选的夹数与它们包含的作品数（给按钮与弹窗做量级提示） */
const checkedFolders = computed(() =>
  folderList.value.filter((f) => checkedFolderIds.value.includes(f.id)),
)
const checkedWorks = computed(() =>
  checkedFolders.value.reduce((sum, f) => sum + (f.total || 0), 0),
)
const folderTotalWorks = computed(() =>
  folderList.value.reduce((sum, f) => sum + (f.total || 0), 0),
)

function toggleAllFolders(checked: boolean) {
  checkedFolderIds.value = checked ? folderList.value.map((f) => f.id) : []
}

/** 全选复选框的 change 回调（Arco 的 value 类型是联合类型，这里收窄成布尔） */
function onToggleAll(checked: boolean | (string | number | boolean)[]) {
  toggleAllFolders(Boolean(checked))
}

/** 「先扫描收藏夹」：只读拉清单 → 打开弹窗（默认全选） */
async function onScanFolders() {
  const data = await scanCollects()
  if (!data) return
  if (!data.folders.length) {
    Message.warning('这个账号下没有收藏夹：可以直接用「一键下载全部收藏」')
    return
  }
  // 夹默认全选：用户只需要取消掉不想要的，而不是一个个勾
  checkedFolderIds.value = data.folders.map((f) => f.id)
  folderModalOpen.value = true
}

/** 弹窗确认：只下载勾选的收藏夹 */
async function onDownloadCheckedFolders() {
  if (!checkedFolderIds.value.length) {
    Message.warning('至少勾选一个收藏夹（不想要的取消勾选即可）')
    return
  }
  const taskId = await submit({
    fetch: true,
    mode: 'collection',
    like_user: likeUser.value.trim() || undefined,
    register_bloggers: registerBloggers.value,
    like_max_counts: safeMaxCounts.value,
    collect_ids: checkedFolderIds.value,
  })
  if (taskId) {
    folderModalOpen.value = false
    emit('submitted')
    await loadStatus({ silent: true })
  }
}

/** 收藏模式的「一键下载全部收藏」：老口径（平铺收藏列表，含未分类与所有收藏夹） */
async function onDownloadAllCollects() {
  const taskId = await submit({
    fetch: true,
    mode: 'collection',
    like_user: likeUser.value.trim() || undefined,
    register_bloggers: registerBloggers.value,
    like_max_counts: safeMaxCounts.value,
  })
  if (taskId) {
    emit('submitted')
    await loadStatus({ silent: true })
  }
}

async function onSubmit() {
  const taskId = await submit({
    fetch: true,
    mode: props.mode,
    like_user: likeUser.value.trim() || undefined,
    register_bloggers: registerBloggers.value,
    // 0 = 全量（显式传，表示本次就要全量，别被配置顶掉）
    like_max_counts: safeMaxCounts.value,
  })
  if (taskId) {
    emit('submitted')
    await loadStatus({ silent: true })
  }
}
</script>

<template>
  <a-card :title="copy.title" size="small" style="margin-bottom: 16px">
    <div class="f2l-row">
      <a-input
        v-model="likeUser"
        placeholder="我的抖音主页链接 或 sec_user_id（如 MS4wLjABAAAA…）"
        allow-clear
        style="max-width: 460px"
        @input="likeUserTouched = true"
      />
      <a-button size="small" :loading="likeUserSaving" :disabled="!dirty" @click="onSaveUser">
        保存
      </a-button>
      <template v-if="isCollect">
        <a-button
          type="primary"
          :loading="collectScanning"
          :disabled="!canSubmit"
          @click="onScanFolders"
        >
          {{ collectScanning ? '正在扫描收藏夹…' : '先扫描收藏夹' }}
        </a-button>
        <a-button :loading="submitting" :disabled="!canSubmit" @click="onDownloadAllCollects">
          一键下载全部收藏
        </a-button>
      </template>
      <a-button
        v-else
        type="primary"
        :loading="submitting"
        :disabled="!canSubmit"
        @click="onSubmit"
      >
        {{ copy.button }}
      </a-button>
    </div>

    <div class="f2l-row f2l-row-max">
      <span class="f2l-label">每次最多翻</span>
      <a-input-number
        v-model="likeMaxCounts"
        :min="0"
        :max="100000"
        :step="50"
        placeholder="0"
        style="width: 130px"
        @input="likeMaxTouched = true"
      />
      <span class="f2l-unit">{{ copy.unit }}</span>
      <a-button size="small" :loading="likeMaxSaving" :disabled="!maxDirty" @click="onSaveMax">
        保存
      </a-button>
      <span class="f2l-option-tip">
        <b>0 = 全量翻到底</b>（默认）。填 100~200 可把日常增量降到一两页： {{ copy.speedTip }}
      </span>
    </div>

    <div v-if="status" class="f2l-status" :class="{ 'is-bad': !available }">
      <a-spin v-if="statusLoading" :size="12" />
      <span>{{ available ? '✅' : '⚠️' }} {{ availabilityReason }}</span>
      <a-link @click="loadStatus()">刷新状态</a-link>
    </div>

    <div class="f2l-options">
      <a-checkbox v-model="registerBloggers">同时登记穿搭博主并绑定素材</a-checkbox>
      <span class="f2l-option-tip">
        未登记的来源作者会自动建成抖音博主（标记「自动登记」）并绑定本批素材；
        它们**不算已登记博主**，所以不会被「一键获取素材」下载——想追踪某个人时，
        去「博主管理」点「纳入追踪」。
      </span>
    </div>

    <div class="f2l-tips">
      <div>
        · {{ copy.listName }}只有本人可见：这里填**你自己**的主页链接（网页版打开你的主页，地址栏
        <code>user/</code> 后面那段就是 sec_user_id），保存后写入 .env，下次直接用
        <template v-if="isCollect">（与「我的喜欢」共用同一个配置）</template>。
      </div>
      <div>
        · 慢的原因不是「按发布时间过滤会漏」，而是 f2 的{{ isCollect ? '收藏' : '点赞' }}模式
        **根本不读 `-i`**（源码实测），且分页没有「遇到已下载就停」——所以提速只能靠上面的
        「每次最多翻」；已下载过的文件 f2 会跳过，入库还有五层判重，重复点击安全。
      </div>
      <div v-if="isCollect">
        · <b>先扫描、后下载</b>：点「先扫描收藏夹」会列出你的收藏夹（名字 + 件数，**默认全选**），
        把不想要的（比如「股票」「哲学」这类）取消勾选，再只下勾选的 —— 没被选中的夹
        一件都不会下载。
      </div>
      <div>· {{ copy.collectTip }}结果浏览与审查在下方「抖音采集历史」里 点「查看结果」。</div>
    </div>

    <a-modal
      v-model:visible="folderModalOpen"
      :title="`选择要下载的收藏夹（共 ${folderList.length} 个）`"
      :ok-loading="submitting"
      ok-text="只下载勾选的"
      cancel-text="取消"
      :width="640"
      @ok="onDownloadCheckedFolders"
    >
      <div class="f2l-collect-head">
        <a-checkbox :model-value="allChecked" @change="onToggleAll">全选 / 全不选</a-checkbox>
        <span class="f2l-option-tip">
          <b>默认全选</b>：把不想要的取消勾选即可。本次将下载 <b>{{ checkedFolders.length }}</b> /
          {{ folderList.length }} 个夹，约 <b>{{ checkedWorks }}</b> /
          {{ folderTotalWorks }} 件作品。
        </span>
      </div>
      <a-checkbox-group v-model="checkedFolderIds" class="f2l-collect-list">
        <a-checkbox v-for="f in folderList" :key="f.id" :value="f.id">
          {{ f.name || '未命名收藏夹' }}（{{ f.total }} 件）
        </a-checkbox>
      </a-checkbox-group>
      <div class="f2l-option-tip f2l-collect-foot">
        · 下载落点、命名模板、判重与「抖音收藏」合集聚合都与原来完全一致；
        每个夹最多取「每次最多翻」条（0 = 该夹全量）。
        <br />
        · 不在任何收藏夹里的「未分类收藏」不在这份清单里 —— 需要的话用卡片上的
        「一键下载全部收藏」。
      </div>
    </a-modal>
  </a-card>
</template>

<style scoped>
.f2l-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.f2l-row-max {
  margin-top: 10px;
}
.f2l-label {
  font-size: 13px;
  color: #4b5563;
}
.f2l-unit {
  font-size: 13px;
  color: #4b5563;
}
.f2l-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f0f9eb;
  color: #2f7d31;
  font-size: 13px;
  flex-wrap: wrap;
}
.f2l-status.is-bad {
  background: #fff7e6;
  color: #a86a00;
}
.f2l-options {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 13px;
}
.f2l-option-tip {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.6;
  flex: 1;
  min-width: 0;
}
.f2l-tips {
  margin-top: 10px;
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.8;
}
.f2l-tips code {
  padding: 0 3px;
  border-radius: 3px;
  background: #f2f3f5;
}
.f2l-collect-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.f2l-collect-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow-y: auto;
  padding: 8px 10px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}
.f2l-collect-foot {
  margin-top: 10px;
  line-height: 1.8;
}
</style>
