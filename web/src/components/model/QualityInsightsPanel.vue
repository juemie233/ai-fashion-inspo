<script setup lang="ts">
/**
 * 质量审核「洞察」面板：把三个已有后端接口接出来，让数据可见。
 *
 * - 按来源通过率：哪个采集源质量差（`pass_rate` 只在已判定素材上算）
 * - 灰区优先复审：初筛置信度 0.5~阈值之间、模型最没把握的样本（比随机抽样信息量高）
 * - prompt A/B：按 prompt 指纹分组比较通过率，历史无指纹的组标注「不可比」
 *
 * 为什么放在一个组件里：三者都是「只读统计 + 小表格」，拆成三个组件会让 props/emit 成本
 * 高于收益；后续任一块变复杂（如加筛选/分页）再单独抽。
 */

import { computed, onMounted, ref } from 'vue'
import apiClient from '@/api/client'
import { sourceLabel } from '@/utils/sourceLabel'
import { qualityReasonLabel } from '@/utils/qualityReason'

interface SourceRow {
  source_type: string
  total: number
  approved: number
  rejected: number
  pending: number
  pass_rate: number
}

interface GrayRow {
  id: string
  confidence: number | null
  quality_status: string | null
  quality_reason_code: string | null
}

interface PromptRow {
  prompt_hash: string
  known: boolean
  total: number
  approved: number
  rejected: number
  pending: number
  pass_rate: number
}

const loading = ref(false)
const sourceRows = ref<SourceRow[]>([])
const grayRows = ref<GrayRow[]>([])
const promptRows = ref<PromptRow[]>([])
const grayThreshold = ref(0)

/** 灰区样本里的素材 ID 只显示前 8 位，避免撑破表格 */
function shortId(id: string): string {
  return id.slice(0, 8)
}

/** prompt 指纹分组：`known=false` 表示历史数据，不可与其它组比较 */
const promptHint = computed(() =>
  promptRows.value.some((r) => !r.known)
    ? '带「历史」标记的组没有 prompt 指纹，不能与其它组直接比较'
    : '同一指纹 = 同一份 prompt，可直接比较通过率',
)

async function loadAll() {
  loading.value = true
  try {
    const [src, gray, ab] = await Promise.allSettled([
      apiClient.get<{ items: SourceRow[] }>('/ai/quality-stats-by-source'),
      apiClient.get<{ items: GrayRow[]; threshold: number }>('/ai/quality-gray', {
        params: { limit: 20 },
      }),
      apiClient.get<{ items: PromptRow[] }>('/ai/quality-prompt-ab'),
    ])
    // 三个接口彼此独立：任一失败不影响其它两块展示（allSettled 而非 all）
    if (src.status === 'fulfilled') sourceRows.value = src.value.data.items
    if (gray.status === 'fulfilled') {
      grayRows.value = gray.value.data.items
      grayThreshold.value = gray.value.data.threshold
    }
    if (ab.status === 'fulfilled') promptRows.value = ab.value.data.items
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <a-card size="small" title="质量洞察" style="margin-bottom: 16px" :loading="loading">
    <template #extra>
      <a-button size="mini" :loading="loading" @click="loadAll">刷新</a-button>
    </template>

    <!-- 按来源通过率 -->
    <div class="insight-block">
      <h4>按来源通过率</h4>
      <div v-if="sourceRows.length === 0" class="insight-empty">暂无已判定的素材</div>
      <a-table
        v-else
        :data="sourceRows"
        :pagination="false"
        size="mini"
        :bordered="false"
        row-key="source_type"
      >
        <template #columns>
          <a-table-column title="来源" data-index="source_type">
            <template #cell="{ record }">{{ sourceLabel(record.source_type) }}</template>
          </a-table-column>
          <a-table-column title="总数" data-index="total" :width="80" />
          <a-table-column title="已通过" data-index="approved" :width="80" />
          <a-table-column title="已拒绝" data-index="rejected" :width="80" />
          <a-table-column title="待审" data-index="pending" :width="80" />
          <a-table-column title="通过率" data-index="pass_rate" :width="90">
            <template #cell="{ record }">{{ record.pass_rate }}%</template>
          </a-table-column>
        </template>
      </a-table>
      <div class="insight-note">通过率只按「已判定」素材计算，待审多不会拉低它。</div>
    </div>

    <!-- 灰区优先复审 -->
    <div class="insight-block">
      <h4>灰区优先复审（初筛置信度 &lt; {{ grayThreshold }}）</h4>
      <div v-if="grayRows.length === 0" class="insight-empty">
        暂无灰区样本（需要先训练初筛器并跑过审核）
      </div>
      <div v-else class="insight-chips">
        <a-tag v-for="row in grayRows" :key="row.id" size="small" color="orange">
          {{ shortId(row.id) }} · {{ row.confidence }}
          <template v-if="row.quality_reason_code">
            · {{ qualityReasonLabel(row.quality_reason_code) }}
          </template>
        </a-tag>
      </div>
      <div class="insight-note">模型差一点就判垃圾的样本，人工看一眼收益远高于随机抽样。</div>
    </div>

    <!-- prompt A/B -->
    <div class="insight-block">
      <h4>Prompt A/B 对比</h4>
      <div v-if="promptRows.length === 0" class="insight-empty">暂无审核日志</div>
      <a-table
        v-else
        :data="promptRows"
        :pagination="false"
        size="mini"
        :bordered="false"
        row-key="prompt_hash"
      >
        <template #columns>
          <a-table-column title="Prompt 指纹" data-index="prompt_hash" :width="140">
            <template #cell="{ record }">
              <a-tag v-if="!record.known" size="small">历史（不可比）</a-tag>
              <span v-else>{{ record.prompt_hash }}</span>
            </template>
          </a-table-column>
          <a-table-column title="审核数" data-index="total" :width="90" />
          <a-table-column title="通过率" data-index="pass_rate" :width="90">
            <template #cell="{ record }">{{ record.pass_rate }}%</template>
          </a-table-column>
        </template>
      </a-table>
      <div class="insight-note">{{ promptHint }}</div>
    </div>
  </a-card>
</template>

<style scoped>
.insight-block + .insight-block {
  margin-top: 14px;
}
.insight-block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-1);
}
.insight-empty {
  font-size: 12px;
  color: var(--color-text-3);
  padding: 4px 0;
}
.insight-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.insight-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-3);
}
</style>
