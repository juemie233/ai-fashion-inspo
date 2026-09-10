<script setup lang="ts">
/** 抖音「一键获取素材」（f2）卡片：增量下载 → 去重 → 入库。
 *
 * 与 CLI（`python -m scripts.import_f2_downloads --fetch --apply`）同一条链路，
 * 这里只是把入口搬到界面上：提交后走后台任务队列，进度在「任务中心」看。
 *
 * 两条约定（与后端一致，界面上明确告知用户）：
 *  - 导入**不做标签分析**：素材以未打标状态入库，打标请用「批量分析任务」
 *  - 入库前**四层去重**：同一内容不会重复入库，重复点击也安全
 */

import { onMounted, ref } from 'vue'
import { useF2Import } from '@/composables/useF2Import'

const { status, statusLoading, submitting, loadStatus, submit } = useF2Import()

// ── 提交选项（默认值即为日常用法：先增量下载，再全量入库）──
const fetchFirst = ref(true)
const authorsText = ref('')
const limit = ref<number | undefined>(undefined)
const skipLive = ref(false)
const makeThumbnails = ref(true)
const showAdvanced = ref(false)

onMounted(loadStatus)

async function onSubmit() {
  const taskId = await submit({
    fetch: fetchFirst.value,
    authors: authorsText.value.trim() || undefined,
    limit: limit.value || undefined,
    skip_live: skipLive.value || undefined,
    make_thumbnails: makeThumbnails.value,
  })
  if (taskId) await loadStatus()
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
        :disabled="!status?.available"
        @click="onSubmit"
      >
        {{ fetchFirst ? '一键获取素材' : '仅入库已下载' }}
      </a-button>
    </div>

    <!-- 可用性状态：不可用时说明原因（f2 未装 / 目录缺失 / 作者库为空） -->
    <a-spin :loading="statusLoading" style="display: block">
      <div v-if="status" class="f2-status" :class="{ 'is-bad': !status.available }">
        <span>{{ status.available ? '✅' : '⚠️' }} {{ status.reason }}</span>
        <a-link @click="loadStatus">刷新状态</a-link>
      </div>
    </a-spin>

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
      <span class="f2-tip">
        下载目录：{{ status?.root || '—' }}；博主清单来自 f2 用户库，共
        {{ status?.authors ?? 0 }} 个
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
