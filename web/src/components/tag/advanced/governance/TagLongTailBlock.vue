<script setup lang="ts">
/** 长尾治理：按「用量分桶」找出只用过一两次的标签，批量合并/删除。
 *
 * 数据事实（2026-10 实测）：22,813 个标签里 56.9% 只用过 1 次、13.6% 用过 2 次，
 * 合计 70.5% 是长尾；而 51 次以上的核心词只有 3.4%。治理动作里「合并到常用标签」
 * 最安全——它会把素材上的关联一并指过去；「删除」则会让那些素材少一个标签。
 */

import { onMounted } from 'vue'
import { useLongTailTags } from '@/composables/useTagGovernance'
import { CATEGORY_LABELS } from '@/constants/tag'

const emit = defineEmits<{ changed: [] }>()

const {
  PREVIEW_LIMIT,
  loading,
  busy,
  bucket,
  selected,
  targetKeyword,
  buckets,
  filtered,
  visibleRows,
  targetTag,
  load,
  onBucketChange,
  onSelectionChange,
  doMerge,
  doDelete,
} = useLongTailTags()

async function onMerge() {
  if (await doMerge()) emit('changed')
}

async function onDelete() {
  if (await doDelete()) emit('changed')
}

onMounted(load)
</script>

<template>
  <div class="tail-block">
    <a-alert type="info" :show-icon="false" style="margin-bottom: 12px">
      标签库 70% 的标签只用过 1~2 次。选中长尾标签后：<strong>合并到常用标签</strong>
      会把素材上的关联一并指过去（推荐）；<strong>删除</strong>会让这些素材少一个标签。
    </a-alert>

    <a-spin :loading="loading" style="display: block">
      <a-space :size="8" wrap style="margin-bottom: 12px">
        <a-radio-group :model-value="bucket" type="button" size="small" @change="onBucketChange">
          <a-radio v-for="b in buckets" :key="b.key" :value="b.key">
            {{ b.label }}（{{ b.count }}）
          </a-radio>
        </a-radio-group>
      </a-space>

      <a-space :size="8" wrap style="margin-bottom: 10px">
        <span class="hint">已选 {{ selected.length }} 个 · 本桶 {{ filtered.length }} 个</span>
        <a-input
          v-model="targetKeyword"
          placeholder="合并到（精确标签名）"
          allow-clear
          style="width: 220px"
          size="small"
        />
        <a-tag v-if="targetTag" color="arcoblue" size="small">
          → {{ targetTag.name }}（{{ targetTag.usage_count }} 次）
        </a-tag>
        <a-popconfirm
          :content="`把选中的 ${selected.length} 个标签合并到「${targetTag?.name ?? ''}」？素材关联会一并指过去。`"
          :disabled="!targetTag || selected.length === 0"
          @ok="onMerge"
        >
          <a-button
            size="small"
            type="primary"
            :loading="busy"
            :disabled="!targetTag || !selected.length"
          >
            合并到目标
          </a-button>
        </a-popconfirm>
        <a-popconfirm
          :content="`删除选中的 ${selected.length} 个标签？这些素材会少一个标签，操作不可恢复。`"
          :disabled="selected.length === 0"
          @ok="onDelete"
        >
          <a-button size="small" status="danger" :loading="busy" :disabled="!selected.length">
            删除选中
          </a-button>
        </a-popconfirm>
      </a-space>

      <a-table
        :data="visibleRows"
        :loading="loading"
        row-key="id"
        :bordered="false"
        :pagination="false"
        size="small"
        :scroll="{ y: 380 }"
        :row-selection="{ type: 'checkbox', showCheckedAll: true }"
        :selected-keys="selected"
        @selection-change="onSelectionChange"
        :columns="[
          { title: '标签', dataIndex: 'name' },
          { title: '类别', dataIndex: 'category', width: 110 },
          { title: '来源', dataIndex: 'source', width: 100 },
          { title: '用量', dataIndex: 'usage_count', width: 80 },
        ]"
      >
        <template #category="{ record }">
          {{ CATEGORY_LABELS[record.category] || record.category }}
        </template>
        <template #source="{ record }">
          {{
            record.source === 'ai_generated'
              ? 'AI 生成'
              : record.source === 'manual'
                ? '手动'
                : '预设'
          }}
        </template>
      </a-table>
      <div v-if="filtered.length > PREVIEW_LIMIT" class="hint">
        仅显示前 {{ PREVIEW_LIMIT }} 个（共
        {{ filtered.length }}）；用主页面的搜索按名称定位其它标签。
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.tail-block {
  padding: 4px 0;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
