<script setup lang="ts">
/** 新标签观察名单：AI 最近新建、还没被用起来的标签 → 人工决定「留下」还是「删掉」。
 *
 * 为什么这是治本的一刀：标签库 22,813 个里 99.9% 是 AI 打标自动建的，`get_or_create_tag`
 * 一打一个新标签——删了还会再长。名单只捞「最近 N 天新建 + 用量 ≤ max_usage」的，
 * 确认保留的改成 manual（人工确认过的词，不再进名单）。数据与动作在 useTagGovernance。
 */

import { onMounted } from 'vue'
import { useWatchList } from '@/composables/useTagGovernance'
import { CATEGORY_LABELS } from '@/constants/tag'

const emit = defineEmits<{ changed: [] }>()

const {
  days,
  maxUsage,
  loading,
  busy,
  items,
  selected,
  cutoff,
  load,
  onSelectionChange,
  doKeep,
  doDelete,
} = useWatchList()

async function onKeep() {
  if (await doKeep()) emit('changed')
}

async function onDelete() {
  if (await doDelete()) emit('changed')
}

onMounted(load)
</script>

<template>
  <div class="watch-block">
    <a-alert type="info" :show-icon="false" style="margin-bottom: 12px">
      AI 打标会自动新建标签，这里是「<strong>最近新建但还没用起来</strong>」的那批：
      确认有用就点「保留」（改成手动标签，从此不再进名单），没用就删掉——这是从源头上
      控制标签库膨胀的唯一入口。
    </a-alert>

    <a-space :size="10" wrap style="margin-bottom: 12px">
      <span class="hint">新建于</span>
      <a-select
        v-model="days"
        :options="[
          { label: '最近 7 天', value: 7 },
          { label: '最近 30 天', value: 30 },
          { label: '最近 90 天', value: 90 },
        ]"
        style="width: 130px"
        size="small"
        @change="load"
      />
      <span class="hint">用量 ≤</span>
      <a-select
        v-model="maxUsage"
        :options="[
          { label: '0 次', value: 0 },
          { label: '1 次', value: 1 },
          { label: '2 次', value: 2 },
          { label: '5 次', value: 5 },
        ]"
        style="width: 100px"
        size="small"
        @change="load"
      />
      <a-button size="small" :loading="loading" @click="load">刷新</a-button>
      <a-popconfirm
        :content="`确认保留选中的 ${selected.length} 个标签？改成手动标签后不再进观察名单。`"
        :disabled="selected.length === 0"
        @ok="onKeep"
      >
        <a-button size="small" type="primary" :disabled="!selected.length" :loading="busy">
          确认保留
        </a-button>
      </a-popconfirm>
      <a-popconfirm
        :content="`删除选中的 ${selected.length} 个标签？相关素材会少这些标签，不可恢复。`"
        :disabled="selected.length === 0"
        @ok="onDelete"
      >
        <a-button size="small" status="danger" :disabled="!selected.length" :loading="busy">
          删除
        </a-button>
      </a-popconfirm>
    </a-space>

    <a-spin :loading="loading" style="display: block">
      <a-empty
        v-if="!loading && items.length === 0"
        :description="`最近 ${days} 天没有「用量 ≤ ${maxUsage}」的新标签，标签库这段时间没被灌新词`"
      />
      <a-table
        v-else
        :data="items"
        row-key="id"
        :bordered="false"
        :pagination="false"
        size="small"
        :scroll="{ y: 400 }"
        :row-selection="{ type: 'checkbox', showCheckedAll: true }"
        :selected-keys="selected"
        @selection-change="onSelectionChange"
        :columns="[
          { title: '标签', dataIndex: 'name' },
          { title: '类别', dataIndex: 'category', width: 120 },
          { title: '用量', dataIndex: 'usage_count', width: 80 },
          { title: '创建时间', dataIndex: 'created_at', width: 170 },
        ]"
      >
        <template #category="{ record }">
          {{ CATEGORY_LABELS[record.category] || record.category }}
        </template>
        <template #created_at="{ record }">
          <span style="white-space: nowrap">{{ String(record.created_at).slice(0, 19) }}</span>
        </template>
      </a-table>
      <div v-if="items.length > 0" class="hint">
        共 {{ items.length }} 个待确认（截止时间 {{ String(cutoff).slice(0, 19) }}）
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.watch-block {
  padding: 4px 0;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
