<script setup lang="ts">
/** 人物频次排行：按关联素材数量降序，辅助识别高频模特/博主。 */

import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, type TableColumnData } from '@arco-design/web-vue'
import { fetchPersonFrequency, type PersonFrequencyItem } from '@/api/admin'
import { sourceLabel } from '@/utils/sourceLabel'

const router = useRouter()
const items = ref<PersonFrequencyItem[]>([])
const loading = ref(false)

const personTypeLabels: Record<string, string> = {
  model: '职业模特',
  blogger: '博主',
}

function typeLabel(type: string): string {
  return personTypeLabels[type] || type
}

/** Arco 表格列定义（render 用 h() 组合节点） */
const columns: TableColumnData[] = [
  { title: '#', width: 48, render: ({ rowIndex }) => String(rowIndex + 1) },
  {
    title: '人物',
    dataIndex: 'name',
    render: ({ record }) =>
      h(
        Button,
        { type: 'text', size: 'mini', onClick: () => router.push(`/persons/${record.id}`) },
        { default: () => record.name },
      ),
  },
  {
    title: '类型',
    dataIndex: 'person_type',
    render: ({ record }) => typeLabel(record.person_type),
  },
  { title: '平台', dataIndex: 'platform', render: ({ record }) => sourceLabel(record.platform) },
  {
    title: '素材数',
    dataIndex: 'count',
    align: 'right',
    render: ({ record }) => String(record.count),
  },
]

async function load() {
  loading.value = true
  try {
    items.value = await fetchPersonFrequency(20)
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <a-card title="人物出现频次" size="small">
    <template #extra>
      <a-button size="mini" type="text" @click="load">刷新</a-button>
    </template>
    <a-spin :loading="loading">
      <a-table
        v-if="items.length > 0"
        :data="items"
        :columns="columns"
        size="small"
        :bordered="false"
        :pagination="false"
        row-key="id"
      />
      <div v-else-if="!loading" class="person-empty">暂无人物关联数据</div>
    </a-spin>
  </a-card>
</template>

<style scoped>
.person-empty {
  color: #999;
  font-size: 13px;
  text-align: center;
  padding: 24px 0;
}
</style>
