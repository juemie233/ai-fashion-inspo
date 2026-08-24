<script setup lang="ts">
/** 历史面板：操作历史分页查询 + 过滤 + before/after 差异查看 + 单条回滚。 */

import { computed, onMounted, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { buildHistoryDiff, formatHistoryValue } from '@/utils/tagHistoryDiff'
import { fetchHistory, rollbackHistory } from '@/api/tagAdvanced'
import { HISTORY_OP_LABELS, type HistoryItem, type HistoryOperation } from '@/types/tagAdvanced'

// ── 过滤 ──
const operation = ref<HistoryOperation | ''>('')
const batchId = ref('')
const tagId = ref<number | undefined>(undefined)

// ── 列表 ──
const items = ref<HistoryItem[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const loading = ref(false)
const rollingBack = ref(false)

// ── 详情弹窗 ──
const detailVisible = ref(false)
const detailItem = ref<HistoryItem | null>(null)

const OPERATION_OPTIONS = Object.entries(HISTORY_OP_LABELS) as Array<[HistoryOperation, string]>

async function loadList(p = 1) {
  loading.value = true
  try {
    const data = await fetchHistory({
      page: p,
      size: size.value,
      operation: operation.value,
      batch_id: batchId.value || undefined,
      tag_id: tagId.value,
    })
    items.value = data.items
    total.value = data.total
    page.value = p
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载历史失败'))
  } finally {
    loading.value = false
  }
}

function onSearch() {
  loadList(1)
}

const diffRows = computed(() => (detailItem.value ? buildHistoryDiff(detailItem.value) : []))

function openDetail(item: HistoryItem) {
  detailItem.value = item
  detailVisible.value = true
}

function confirmRollback(item: HistoryItem) {
  const label = HISTORY_OP_LABELS[item.operation] ?? item.operation
  Modal.confirm({
    title: '确认回滚',
    content: `回滚该条「${label}」操作（影响 ${item.tag_ids.length} 个标签）？\n若标签在操作后已被修改，将拒绝回滚。`,
    onOk: async () => {
      rollingBack.value = true
      try {
        const data = await rollbackHistory(item.id)
        Message.success(data.message)
        detailVisible.value = false
        loadList(page.value)
      } catch (e) {
        Message.error(getApiErrorMessage(e, '回滚失败'))
      } finally {
        rollingBack.value = false
      }
    },
  })
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 19).replace('T', ' ')
}

onMounted(() => {
  loadList(1)
})
</script>

<template>
  <div class="history-panel">
    <!-- 过滤栏 -->
    <div class="history-filters">
      <a-select v-model="operation" placeholder="全部操作" :allow-clear="true" style="width: 140px">
        <a-option v-for="[key, label] in OPERATION_OPTIONS" :key="key" :value="key">
          {{ label }}
        </a-option>
      </a-select>
      <a-input v-model="batchId" placeholder="批次 ID" allow-clear style="width: 200px" />
      <a-input-number v-model="tagId" placeholder="标签 ID" style="width: 120px" />
      <a-button type="primary" size="small" @click="onSearch">查询</a-button>
      <span class="history-total">共 {{ total }} 条</span>
    </div>

    <!-- 列表 -->
    <a-spin :loading="loading">
      <a-table :data="items" :pagination="false" size="small">
        <template #columns>
          <a-table-column title="时间" :width="150">
            <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
          </a-table-column>
          <a-table-column title="操作" :width="90">
            <template #cell="{ record }">
              <a-tag>{{
                HISTORY_OP_LABELS[record.operation as HistoryOperation] ?? record.operation
              }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="影响标签" data-index="tag_ids" />
          <a-table-column title="批次" :width="200">
            <template #cell="{ record }">{{ record.batch_id ?? '—' }}</template>
          </a-table-column>
          <a-table-column title="操作" :width="150">
            <template #cell="{ record }">
              <a-space>
                <a-button size="mini" type="text" @click="openDetail(record)">详情</a-button>
                <a-button
                  size="mini"
                  type="text"
                  status="danger"
                  :loading="rollingBack"
                  @click="confirmRollback(record)"
                >
                  回滚
                </a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
      <a-pagination
        v-if="total > size"
        :total="total"
        :page-size="size"
        :current="page"
        show-total
        class="history-pager"
        @change="loadList"
      />
    </a-spin>

    <!-- 详情弹窗 -->
    <a-modal v-model:visible="detailVisible" title="操作详情" :footer="false" :width="640">
      <div v-if="detailItem" class="detail-meta">
        <a-tag>{{ HISTORY_OP_LABELS[detailItem.operation] ?? detailItem.operation }}</a-tag>
        <span>{{ formatTime(detailItem.created_at) }}</span>
        <span v-if="detailItem.batch_id">批次 {{ detailItem.batch_id }}</span>
      </div>
      <div v-for="row in diffRows" :key="row.tag_id" class="diff-row">
        <div class="diff-tag">
          <a-tag :color="row.deleted ? 'red' : 'arcoblue'">
            {{ row.deleted ? `已删除：${row.name}` : row.name }}
          </a-tag>
        </div>
        <table v-if="row.changes.length" class="diff-table">
          <thead>
            <tr>
              <th>字段</th>
              <th>操作前</th>
              <th>操作后</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in row.changes" :key="c.field">
              <td>{{ c.label }}</td>
              <td class="diff-before">{{ formatHistoryValue(c.before) }}</td>
              <td class="diff-after">{{ formatHistoryValue(c.after) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="diff-empty">字段无变化</div>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.history-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}
.history-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.history-total {
  font-size: 12px;
  color: #9ca3af;
}
.history-pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.detail-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #6b7280;
}
.diff-row {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
}
.diff-tag {
  margin-bottom: 8px;
}
.diff-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.diff-table th,
.diff-table td {
  border: 1px solid #f0f0f0;
  padding: 6px 8px;
  text-align: left;
}
.diff-table th {
  background: #fafafa;
  color: #6b7280;
  font-weight: 500;
}
.diff-before {
  color: #9ca3af;
  text-decoration: line-through;
}
.diff-after {
  color: #1baf7a;
  font-weight: 500;
}
.diff-empty {
  font-size: 12px;
  color: #9ca3af;
}
</style>
