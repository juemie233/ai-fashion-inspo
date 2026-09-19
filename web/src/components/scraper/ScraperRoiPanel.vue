<script setup lang="ts">
/** 采集 ROI 漏斗：按关键词 / 博主看「采集量 → 入库量 → 质量合格率」，可折叠卡片。 */

import { computed, nextTick, ref } from 'vue'
import {
  ROI_CHANNEL_LABELS,
  ROI_RANGES,
  formatCount,
  formatRate,
  useScraperRoi,
} from '@/composables/useScraperRoi'

const { roi, loading, days, dimension, loadRoi, hasRows, notes } = useScraperRoi()

/** 是否展开看板（默认折叠，与采集统计看板一致） */
const expanded = ref(false)

/** 关键词维度列 */
const keywordColumns = [
  { title: '关键词', dataIndex: 'keyword', slotName: 'label', width: 160 },
  { title: '任务', dataIndex: 'tasks', width: 64 },
  { title: '发现', dataIndex: 'found', width: 72 },
  { title: '入库', dataIndex: 'added', width: 72 },
  { title: '入库率', dataIndex: 'add_rate', slotName: 'addRate', width: 84 },
  { title: '可归属', dataIndex: 'attributed', slotName: 'attributed', width: 84 },
  { title: '通过', dataIndex: 'approved', width: 64 },
  { title: '不合格', dataIndex: 'rejected', width: 72 },
  { title: '待审', dataIndex: 'pending', width: 64 },
  { title: '合格率(已审)', dataIndex: 'approved_rate', slotName: 'approvedRate', width: 100 },
]

/** 博主维度列（f2 行没有任务级口径，对应列显示 —） */
const authorColumns = [
  { title: '博主', dataIndex: 'name', slotName: 'author', width: 170 },
  { title: '通道', dataIndex: 'channel', slotName: 'channel', width: 96 },
  { title: '任务', dataIndex: 'tasks', slotName: 'tasks', width: 64 },
  { title: '发现', dataIndex: 'found', slotName: 'found', width: 72 },
  { title: '入库', dataIndex: 'added', slotName: 'added', width: 72 },
  { title: '入库率', dataIndex: 'add_rate', slotName: 'addRate', width: 84 },
  { title: '素材数', dataIndex: 'imported', width: 72 },
  { title: '通过', dataIndex: 'approved', width: 64 },
  { title: '不合格', dataIndex: 'rejected', width: 72 },
  { title: '待审', dataIndex: 'pending', width: 64 },
  { title: '合格率(已审)', dataIndex: 'approved_rate', slotName: 'approvedRate', width: 100 },
]

/** 口径概览：扫了多少任务、有多少素材能归属到质量审核 */
const coverageText = computed(() => {
  const c = roi.value?.coverage
  if (!c) return ''
  return (
    `窗口内扫描 ${c.tasks_scanned} 个采集任务 · 可归属质量审核的素材 ${c.keyword_attributed} 条` +
    ` · f2 抖音素材 ${c.f2_materials} 条`
  )
})

function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) {
    nextTick(() => setTimeout(() => loadRoi(), 30))
  }
}

async function refresh() {
  await loadRoi()
}
</script>

<template>
  <a-card size="small" style="margin-bottom: 16px">
    <template #title>
      <div
        style="
          display: flex;
          align-items: center;
          justify-content: space-between;
          cursor: pointer;
          user-select: none;
        "
        @click="toggle"
      >
        <span>🎯 采集 ROI（成本花在哪 · {{ days }} 天）{{ expanded ? '▼' : '▶' }}</span>
        <a-button v-if="expanded" size="mini" type="text" @click.stop="refresh">刷新</a-button>
      </div>
    </template>

    <template v-if="expanded">
      <div class="roi-toolbar">
        <a-radio-group v-model="dimension" type="button" size="small" @change="loadRoi">
          <a-radio value="keyword">按关键词</a-radio>
          <a-radio value="author">按博主</a-radio>
        </a-radio-group>
        <a-select v-model="days" size="small" style="width: 120px" @change="loadRoi">
          <a-option v-for="r in ROI_RANGES" :key="r.value" :value="r.value">
            {{ r.label }}
          </a-option>
        </a-select>
      </div>

      <a-spin :loading="loading" style="display: block">
        <template v-if="dimension === 'keyword'">
          <a-table
            v-if="roi && roi.by_keyword.length"
            :columns="keywordColumns"
            :data="roi.by_keyword"
            :pagination="false"
            row-key="keyword"
            size="small"
            :scroll="{ x: 900 }"
          >
            <template #label="{ record }">
              <span class="roi-name">{{ record.keyword }}</span>
            </template>
            <template #addRate="{ record }">
              <span :class="{ 'roi-null': record.add_rate === null }">
                {{ formatRate(record.add_rate) }}
              </span>
            </template>
            <template #attributed="{ record }">
              <span :class="{ 'roi-null': record.attributed === 0 }">
                {{ record.attributed === 0 ? '暂无样本' : record.attributed }}
              </span>
            </template>
            <template #approvedRate="{ record }">
              <span :class="{ 'roi-null': record.approved_rate === null }">
                {{ formatRate(record.approved_rate) }}
              </span>
            </template>
          </a-table>
          <div v-else-if="!loading" class="roi-empty">窗口内暂无按关键词的采集任务</div>
        </template>

        <template v-else>
          <a-table
            v-if="roi && roi.by_author.length"
            :columns="authorColumns"
            :data="roi.by_author"
            :pagination="false"
            :row-key="(row: Record<string, unknown>) => `${row.channel}-${row.name}`"
            size="small"
            :scroll="{ x: 1020 }"
          >
            <template #author="{ record }">
              <span class="roi-name">{{ record.name }}</span>
            </template>
            <template #channel="{ record }">
              <a-tag size="small" :color="record.channel === 'f2' ? 'arcoblue' : 'orangered'">
                {{ ROI_CHANNEL_LABELS[record.channel] || record.channel }}
              </a-tag>
            </template>
            <template #tasks="{ record }">{{ formatCount(record.tasks) }}</template>
            <template #found="{ record }">{{ formatCount(record.found) }}</template>
            <template #added="{ record }">{{ formatCount(record.added) }}</template>
            <template #addRate="{ record }">
              <span :class="{ 'roi-null': record.add_rate === null }">
                {{ formatRate(record.add_rate) }}
              </span>
            </template>
            <template #approvedRate="{ record }">
              <span :class="{ 'roi-null': record.approved_rate === null }">
                {{ formatRate(record.approved_rate) }}
              </span>
            </template>
          </a-table>
          <div v-else-if="!loading" class="roi-empty">窗口内暂无采集数据</div>
        </template>

        <div v-if="hasRows && coverageText" class="roi-coverage">{{ coverageText }}</div>

        <a-alert
          v-for="(note, i) in notes"
          :key="i"
          type="normal"
          size="small"
          style="margin-top: 8px"
        >
          {{ note }}
        </a-alert>
      </a-spin>
    </template>
  </a-card>
</template>

<style scoped>
.roi-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.roi-name {
  font-weight: 500;
}
.roi-null {
  color: #bbb;
}
.roi-coverage {
  margin-top: 10px;
  font-size: 12px;
  color: #999;
}
.roi-empty {
  text-align: center;
  color: #999;
  padding: 24px 0;
  font-size: 13px;
}
</style>
