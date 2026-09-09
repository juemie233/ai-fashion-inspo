<script setup lang="ts">
/**
 * 提示词版本质量看板（数据洞察页）：按「提示词版本 × 模型」对比打标质量。
 *
 * 指标口径：
 * - 成功率 / 平均标签数：窗口内标签分析的成功比例与产出标签数（结构化快照）；
 * - 纠错率：每百次分析的人工纠错反馈数（素材详情页「标错了」）；
 * - 裸词率（可选）：采样重放模型原始响应，统计命中命名口径规则的裸词占比
 *   ——反映模型原始命名质量（快照已过滤，看不到），需重放故默认关闭。
 */

import { onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { fetchPromptQuality, type PromptQualityItem } from '@/api/admin'
import { renderTimeCell, formatDate } from '@/utils/format'

const days = ref(30)
const includeBareRate = ref(false)
const loading = ref(false)
const items = ref<PromptQualityItem[]>([])
const scannedAt = ref('')

async function load() {
  loading.value = true
  try {
    const data = await fetchPromptQuality(days.value, includeBareRate.value)
    items.value = data.items
    scannedAt.value = new Date().toISOString()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载提示词质量数据失败'))
  } finally {
    loading.value = false
  }
}

/** 成功率颜色：<90% 标红，<97% 标橙 */
function rateColor(v: number): string | undefined {
  if (v < 90) return 'rgb(var(--danger-6))'
  if (v < 97) return 'rgb(var(--warning-6))'
  return undefined
}

/** 裸词率颜色：越高越差（>5% 红，>2% 橙） */
function bareColor(v: number): string | undefined {
  if (v > 5) return 'rgb(var(--danger-6))'
  if (v > 2) return 'rgb(var(--warning-6))'
  return undefined
}

/** Arco 表格排序配置（sortable 需对象形式，布尔值类型不匹配） */
const SORTABLE: { sortDirections: ('ascend' | 'descend')[] } = {
  sortDirections: ['ascend', 'descend'],
}

onMounted(load)
</script>

<template>
  <a-card class="prompt-quality" title="提示词质量对比">
    <template #extra>
      <a-space>
        <a-radio-group v-model="days" type="button" size="small" @change="load">
          <a-radio :value="7">7 天</a-radio>
          <a-radio :value="30">30 天</a-radio>
          <a-radio :value="90">90 天</a-radio>
        </a-radio-group>
        <a-checkbox v-model="includeBareRate" @change="load">计算裸词率</a-checkbox>
        <a-button size="small" :loading="loading" @click="load">刷新</a-button>
      </a-space>
    </template>

    <p class="hint">
      按「提示词版本 × 模型」对比：纠错率 = 每百次分析的人工纠错数（素材详情页「标错了」）； 裸词率
      = 采样重放模型原始响应中命中命名口径的裸词占比（快照已过滤，需重放，较慢）。
    </p>

    <a-table
      :data="items"
      :loading="loading"
      :pagination="{ pageSize: 15, showTotal: true }"
      size="small"
      row-key="prompt_version"
      :scroll="{ x: 1080 }"
    >
      <template #columns>
        <a-table-column title="提示词版本" :width="240">
          <template #cell="{ record }">
            <div class="version-cell">
              <span class="version-label">{{ record.version_label }}</span>
              <span class="version-hash">{{ record.prompt_version || '无' }}</span>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="模型" :width="180" data-index="model_name" />
        <a-table-column
          title="分析次数"
          :width="100"
          align="right"
          data-index="analyses"
          :sortable="SORTABLE"
        />
        <a-table-column title="成功率" :width="100" align="right" :sortable="SORTABLE">
          <template #cell="{ record }">
            <span :style="{ color: rateColor(record.success_rate) }">
              {{ record.success_rate }}%
            </span>
          </template>
        </a-table-column>
        <a-table-column
          title="平均标签数"
          :width="110"
          align="right"
          data-index="avg_tags"
          :sortable="SORTABLE"
        />
        <a-table-column
          title="纠错数"
          :width="90"
          align="right"
          data-index="corrections"
          :sortable="SORTABLE"
        />
        <a-table-column title="纠错率" :width="100" align="right" :sortable="SORTABLE">
          <template #cell="{ record }">
            {{ record.correction_rate }}
            <span class="unit">/百次</span>
          </template>
        </a-table-column>
        <a-table-column
          v-if="includeBareRate"
          title="裸词率"
          :width="100"
          align="right"
          :sortable="SORTABLE"
        >
          <template #cell="{ record }">
            <span :style="{ color: bareColor(record.bare_rate ?? 0) }">
              {{ record.bare_rate ?? '--' }}%
            </span>
          </template>
        </a-table-column>
        <a-table-column title="最近使用" :width="170">
          <template #cell="{ record }">
            {{ renderTimeCell(formatDate(record.last_used_at)) }}
          </template>
        </a-table-column>
      </template>
    </a-table>

    <a-empty v-if="!loading && items.length === 0" description="窗口内暂无分析记录" />
  </a-card>
</template>

<style scoped>
.prompt-quality {
  width: 100%;
}
.hint {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.6;
}
.version-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}
.version-label {
  font-size: 13px;
}
.version-hash {
  font-size: 11px;
  color: var(--color-text-3);
  font-family: monospace;
}
.unit {
  font-size: 11px;
  color: var(--color-text-3);
}
</style>
