<script setup lang="ts">
/** 抖音「采集我的喜欢」（点赞作品）卡片：拉取自己的点赞列表 → 去重 → 入库。
 *
 * 与「一键获取素材」（博主主页作品）的差别只在**下载入口**：
 *  - f2 的 `-M like` 拉的是登录账号自己的点赞列表，所以必须填**你自己**的主页链接
 *    （点赞列表只有本人可见；填一次即写入 .env，之后自动带上）
 *  - 喜欢列表按**点赞时间**排序，而 f2 的时间窗口按**作品发布时间**过滤，所以这里
 *    全量翻页（比发布模式慢）；已下载过的文件 f2 会跳过
 *  - 入库完全复用同一条链路：五层判重（内容哈希 / 垃圾桶 / 批次内 / 平台 ID / 参数），
 *    同一作品从主页与喜欢两条路进来都不会重复；结果审查也在下方「抖音采集历史」里
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useF2Import } from '@/composables/useF2Import'

const emit = defineEmits<{ (e: 'submitted'): void }>()

const { status, statusLoading, submitting, likeUserSaving, loadStatus, submit, setLikeUser } =
  useF2Import()

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

const savedLikeUser = computed(() => status.value?.like_user ?? '')
const dirty = computed(() => likeUser.value.trim() !== savedLikeUser.value)
const canSubmit = computed(
  () => Boolean(status.value?.like_available) && Boolean(likeUser.value.trim()),
)

onMounted(loadStatus)

async function onSaveUser() {
  const ok = await setLikeUser(likeUser.value.trim())
  if (ok) likeUserTouched = false
}

/** 采集选项：入库后自动登记来源作者为穿搭博主（默认开） */
const registerBloggers = ref(true)

async function onSubmit() {
  const taskId = await submit({
    fetch: true,
    mode: 'like',
    like_user: likeUser.value.trim() || undefined,
    register_bloggers: registerBloggers.value,
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
        · 喜欢列表按点赞时间排序、f2 的时间窗口按发布时间过滤，因此本入口**全量翻页**
        （比「一键获取素材」慢）；已下载过的文件 f2 会跳过，入库还有五层判重，重复点击安全。
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
