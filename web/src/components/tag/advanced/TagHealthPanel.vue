<script setup lang="ts">
/** 健康度面板：评分卡 + 四类问题明细 + 一键操作（扫描 / 分页 / 批量编辑 / 删除 / 重复对合并）。 */

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { asHealthResult, fetchHealthIssue, scanHealth } from '@/api/tagAdvanced'
import { batchDeleteTags, mergeTags } from '@/api/tags'
import { CATEGORY_LABELS, SOURCE_LABELS } from '@/api/tags'
import { useTaskPolling } from '@/composables/useTaskPolling'
import { useTagAdvanced } from '@/composables/useTagAdvanced'
import {
  HEALTH_ISSUE_LABELS,
  type DuplicateIssuePair,
  type HealthIssueItem,
  type HealthIssueType,
} from '@/types/tagAdvanced'

const { task, pollTask, stopPolling } = useTaskPolling()
const { openBatchEdit } = useTagAdvanced()

// ── 状态 ──
const score = ref<number | null>(null)
const issueCounts = ref<Record<HealthIssueType, number>>({
  orphan: 0,
  low_frequency: 0,
  low_quality_name: 0,
  duplicate: 0,
})
const scannedAt = ref('')
const activeIssueType = ref<HealthIssueType>('orphan')
const items = ref<HealthIssueItem[]>([])
const duplicatePairs = ref<DuplicateIssuePair[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const scanning = ref(false)
const loadingDetail = ref(false)
const selectedIds = ref<number[]>([])

const running = computed(
  () => scanning.value || Boolean(task.value && ['pending', 'running'].includes(task.value.status)),
)

const ISSUE_TYPES: HealthIssueType[] = ['orphan', 'low_frequency', 'low_quality_name', 'duplicate']

const isDuplicate = computed(() => activeIssueType.value === 'duplicate')

function scoreColor(s: number): string {
  if (s >= 85) return '#1baf7a'
  if (s >= 60) return '#eda100'
  return '#e34948'
}

/** 提交扫描任务并轮询 */
async function runScan() {
  if (scanning.value) return
  scanning.value = true
  try {
    const { task_id } = await scanHealth(0.75)
    pollTask(task_id, (result) => {
      const r = asHealthResult(result)
      if (r) {
        score.value = r.score
        scannedAt.value = r.scanned_at
        for (const t of ISSUE_TYPES) {
          issueCounts.value[t] = r.issues[t]?.count ?? 0
        }
        Message.success(`健康度扫描完成：${r.total} 个标签，评分 ${r.score}`)
        loadIssue(activeIssueType.value, 1)
      }
    })
  } catch (e) {
    Message.error(getApiErrorMessage(e, '提交扫描任务失败'))
  } finally {
    scanning.value = false
  }
}

/** 加载某问题类型的明细（分页） */
async function loadIssue(type: HealthIssueType, p = 1) {
  loadingDetail.value = true
  try {
    const data = await fetchHealthIssue(type, p, pageSize.value)
    page.value = data.page
    total.value = data.total
    if (type === 'duplicate') {
      duplicatePairs.value = data.items as DuplicateIssuePair[]
      items.value = []
    } else {
      items.value = data.items as HealthIssueItem[]
      duplicatePairs.value = []
    }
    selectedIds.value = []
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载健康度明细失败'))
  } finally {
    loadingDetail.value = false
  }
}

function onIssueTypeChange() {
  loadIssue(activeIssueType.value, 1)
}

function onIssueTypeClick(type: HealthIssueType) {
  activeIssueType.value = type
  loadIssue(type, 1)
}

function onPageChange(p: number) {
  loadIssue(activeIssueType.value, p)
}

// ── 操作 ──

/** 删除勾选的标签（孤儿/低频/低质命名） */
async function deleteSelected() {
  const ids = selectedIds.value
  if (!ids.length) {
    Message.warning('请先勾选标签')
    return
  }
  Modal.confirm({
    title: '确认删除',
    content: `确定删除选中的 ${ids.length} 个标签吗？删除后不可恢复。`,
    onOk: async () => {
      try {
        await batchDeleteTags(ids)
        Message.success('已删除')
        loadIssue(activeIssueType.value, page.value)
      } catch (e) {
        Message.error(getApiErrorMessage(e, '删除失败'))
      }
    },
  })
}

/** 把勾选标签送入批量编辑抽屉 */
function editSelected() {
  const ids = selectedIds.value
  if (!ids.length) {
    Message.warning('请先勾选标签')
    return
  }
  openBatchEdit({ tag_ids: ids })
}

/** 疑似重复对：合并到指定一侧 */
async function mergePair(pair: DuplicateIssuePair, target: 'tag_a' | 'tag_b') {
  const source = target === 'tag_a' ? pair.tag_b : pair.tag_a
  const dest = target === 'tag_a' ? pair.tag_a : pair.tag_b
  if (!source || !dest) return
  Modal.confirm({
    title: '确认合并',
    content: `将「${source.name}」合并到「${dest.name}」？`,
    onOk: async () => {
      try {
        await mergeTags(source.id, dest.id)
        Message.success('合并完成')
        loadIssue('duplicate', page.value)
      } catch (e) {
        Message.error(getApiErrorMessage(e, '合并失败'))
      }
    },
  })
}

function onSelectionChange(keys: Array<string | number>) {
  selectedIds.value = keys.map(Number)
}

onMounted(() => {
  runScan()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="health-panel">
    <!-- 评分卡 + 问题计数 -->
    <div class="health-top">
      <div class="score-card">
        <div class="score-label">健康评分</div>
        <div class="score-value" :style="{ color: scoreColor(score ?? 100) }">
          {{ score ?? '--' }}
        </div>
        <div class="score-sub">
          <template v-if="scannedAt"
            >扫描时间 {{ scannedAt.slice(0, 16).replace('T', ' ') }}</template
          >
          <template v-else>尚未扫描</template>
        </div>
      </div>
      <div
        v-for="t in ISSUE_TYPES"
        :key="t"
        class="issue-chip"
        :class="{ active: activeIssueType === t }"
        @click="onIssueTypeClick(t)"
      >
        <div class="chip-name">{{ HEALTH_ISSUE_LABELS[t] }}</div>
        <div class="chip-count">{{ issueCounts[t] }}</div>
      </div>
      <a-button type="primary" :loading="running" @click="runScan"> 重新扫描 </a-button>
    </div>

    <!-- 明细列表 -->
    <div class="health-list">
      <div class="list-header">
        <span class="list-title">{{ HEALTH_ISSUE_LABELS[activeIssueType] }}</span>
        <span class="list-total">共 {{ total }} 条</span>
      </div>

      <a-spin :loading="loadingDetail">
        <!-- 疑似重复对 -->
        <a-table
          v-if="isDuplicate"
          :data="duplicatePairs"
          :pagination="false"
          size="small"
          row-key="tag_a.id"
        >
          <template #columns>
            <a-table-column title="标签 A" data-index="tag_a.name" />
            <a-table-column title="标签 B" data-index="tag_b.name" />
            <a-table-column title="相似度" :width="100">
              <template #cell="{ record }"> {{ (record.similarity * 100).toFixed(0) }}% </template>
            </a-table-column>
            <a-table-column title="操作" :width="180">
              <template #cell="{ record }">
                <a-space>
                  <a-button size="mini" type="text" @click="mergePair(record, 'tag_a')">
                    合并到 A
                  </a-button>
                  <a-button size="mini" type="text" @click="mergePair(record, 'tag_b')">
                    合并到 B
                  </a-button>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>

        <!-- 常规问题列表 -->
        <template v-else>
          <a-table
            :data="items"
            :pagination="false"
            size="small"
            :row-selection="{ selectedRowKeys: selectedIds }"
            @selection-change="onSelectionChange"
          >
            <template #columns>
              <a-table-column title="标签名" data-index="name" />
              <a-table-column title="类别" :width="100">
                <template #cell="{ record }">{{
                  CATEGORY_LABELS[record.category] ?? record.category
                }}</template>
              </a-table-column>
              <a-table-column title="来源" :width="100">
                <template #cell="{ record }">{{
                  SOURCE_LABELS[record.source] ?? record.source
                }}</template>
              </a-table-column>
              <a-table-column title="使用次数" :width="90" data-index="usage_count" />
              <a-table-column
                v-if="activeIssueType === 'low_quality_name'"
                title="原因"
                data-index="reason"
              />
            </template>
          </a-table>
          <div class="batch-bar">
            <a-space>
              <a-button size="small" :disabled="!selectedIds.length" @click="editSelected">
                批量编辑
              </a-button>
              <a-button
                size="small"
                status="danger"
                :disabled="!selectedIds.length"
                @click="deleteSelected"
              >
                批量删除
              </a-button>
            </a-space>
          </div>
        </template>

        <!-- 分页 -->
        <a-pagination
          v-if="total > pageSize"
          :total="total"
          :page-size="pageSize"
          :current="page"
          show-total
          class="issue-pager"
          @change="onPageChange"
        />
      </a-spin>
    </div>
  </div>
</template>

<style scoped>
.health-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.health-top {
  display: flex;
  align-items: stretch;
  gap: 10px;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.score-card {
  min-width: 140px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 14px;
}
.score-label {
  font-size: 12px;
  color: #6b7280;
}
.score-value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.2;
}
.score-sub {
  font-size: 11px;
  color: #9ca3af;
}
.issue-chip {
  flex: 1;
  min-width: 110px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.issue-chip.active {
  border-color: #2a78d6;
  background: #eef4fd;
}
.chip-name {
  font-size: 12px;
  color: #6b7280;
}
.chip-count {
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}
.health-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.list-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.list-title {
  font-size: 14px;
  font-weight: 600;
}
.list-total {
  font-size: 12px;
  color: #9ca3af;
}
.batch-bar {
  margin-top: 8px;
}
.issue-pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
