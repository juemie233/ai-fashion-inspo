<script setup lang="ts">
/** 抖音「采集我的喜欢」（点赞作品）卡片：拉取自己的点赞列表 → 去重 → 入库。
 *
 * 与「一键获取素材」（博主主页作品）的差别只在**下载入口**：
 *  - f2 的 `-M like` 拉的是登录账号自己的点赞列表，所以必须填**你自己**的主页链接
 *    （点赞列表只有本人可见；填一次即写入 .env，之后自动带上）
 *  - **不能靠日期窗口提速**：f2 的点赞模式根本不读 `-i`（源码实测，不是"按发布时间
 *    过滤会漏"的问题，而是这个参数在此模式下无效）。真正能收窄翻页量的是
 *    `-o/--max-counts`（本卡片的「每次最多翻」），因为 f2 的点赞分页没有
 *    「遇到已下载就停」、每页还要固定等一次 timeout
 *  - 入库完全复用同一条链路：五层判重（内容哈希 / 垃圾桶 / 批次内 / 平台 ID / 参数），
 *    同一作品从主页与喜欢两条路进来都不会重复；结果审查也在下方「抖音采集历史」里
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useF2Import } from '@/composables/useF2Import'

const emit = defineEmits<{ (e: 'submitted'): void }>()

const {
  status,
  statusLoading,
  submitting,
  likeUserSaving,
  likeMaxSaving,
  loadStatus,
  submit,
  setLikeUser,
  setLikeMaxCounts,
} = useF2Import()

/** 「我的主页链接 / sec_user_id」输入框：初始值取后端已保存的配置 */
const likeUser = ref('')

// 用户没动过输入框时才回填（避免轮询把正在输入的内容覆盖掉）
let likeUserTouched = false
watch(
  () => status.value?.like_user,
  (value) => {
    if (likeUserTouched || value == null) return
    likeUser.value = value
  },
  { immediate: true },
)

/** 「每次最多翻多少条点赞」：0=全量；初始值取后端已保存的配置 */
const likeMaxCounts = ref(0)

let likeMaxTouched = false
watch(
  () => status.value?.like_max_counts,
  (value) => {
    if (likeMaxTouched || value == null) return
    likeMaxCounts.value = value
  },
  { immediate: true },
)

const savedLikeUser = computed(() => status.value?.like_user ?? '')
const savedLikeMax = computed(() => status.value?.like_max_counts ?? 0)
const dirty = computed(() => likeUser.value.trim() !== savedLikeUser.value)
const maxDirty = computed(() => (likeMaxCounts.value || 0) !== savedLikeMax.value)
const canSubmit = computed(
  () => Boolean(status.value?.like_available) && Boolean(likeUser.value.trim()),
)

/** 输入非数字/负数时的兜底：按 0（全量）处理，避免把非法值发给后端 */
const safeMaxCounts = computed(() => {
  const n = Number(likeMaxCounts.value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
})

onMounted(loadStatus)

async function onSaveUser() {
  const ok = await setLikeUser(likeUser.value.trim())
  if (ok) likeUserTouched = false
}

async function onSaveMax() {
  const ok = await setLikeMaxCounts(safeMaxCounts.value)
  if (ok) {
    likeMaxTouched = false
    likeMaxCounts.value = safeMaxCounts.value
  }
}

/** 采集选项：入库后自动登记来源作者为穿搭博主（默认开） */
const registerBloggers = ref(true)

async function onSubmit() {
  const taskId = await submit({
    fetch: true,
    mode: 'like',
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
  <a-card title="采集我的喜欢（抖音点赞）" size="small" style="margin-bottom: 16px">
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
      <a-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="onSubmit">
        采集我的喜欢
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
      <span class="f2l-unit">条点赞</span>
      <a-button size="small" :loading="likeMaxSaving" :disabled="!maxDirty" @click="onSaveMax">
        保存
      </a-button>
      <span class="f2l-option-tip">
        <b>0 = 全量翻到底</b>（默认）。填 100~200 可把日常增量降到一两页： f2
        的点赞分页**没有「遇到已下载就停」**，从最新一路翻到底、每页还固定等一次 timeout（本机 10
        秒），全量时零新增也要空翻几分钟。代价：两次运行之间新增点赞
        超过该条数时会漏，攒了很久没采就填大一点或填 0 全量。
      </span>
    </div>

    <div v-if="status" class="f2l-status" :class="{ 'is-bad': !status.like_available }">
      <a-spin v-if="statusLoading" :size="12" />
      <span>{{ status.like_available ? '✅' : '⚠️' }} {{ status.like_reason }}</span>
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
        · 点赞列表只有本人可见：这里填**你自己**的主页链接（网页版打开你的主页，地址栏
        <code>user/</code> 后面那段就是 sec_user_id），保存后写入 .env，下次直接用。
      </div>
      <div>
        · 慢的原因不是「按发布时间过滤会漏」，而是 f2 的点赞模式**根本不读 `-i`**
        （源码实测），且点赞分页没有「遇到已下载就停」——所以提速只能靠上面的
        「每次最多翻」；已下载过的文件 f2 会跳过，入库还有五层判重，重复点击安全。
      </div>
      <div>
        · 入库不做标签分析（素材为未打标状态）；结果浏览与审查在下方「抖音采集历史」里
        点「查看结果」。
      </div>
    </div>
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
</style>
