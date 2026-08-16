<script setup lang="ts">
/** 人物频次排行：按关联素材数量降序，辅助识别高频模特/博主。 */

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
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
  <n-card title="人物出现频次" size="small">
    <template #header-extra>
      <n-button size="tiny" quaternary @click="load">刷新</n-button>
    </template>
    <n-spin :show="loading">
      <n-table v-if="items.length > 0" size="small" :bordered="false">
        <thead>
          <tr>
            <th>#</th>
            <th>人物</th>
            <th>类型</th>
            <th>平台</th>
            <th style="text-align: right">素材数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in items" :key="p.id">
            <td>{{ i + 1 }}</td>
            <td>
              <n-button text size="tiny" type="primary" @click="router.push(`/persons/${p.id}`)">
                {{ p.name }}
              </n-button>
            </td>
            <td>{{ typeLabel(p.person_type) }}</td>
            <td>{{ sourceLabel(p.platform) }}</td>
            <td style="text-align: right">{{ p.count }}</td>
          </tr>
        </tbody>
      </n-table>
      <div v-else-if="!loading" class="person-empty">暂无人物关联数据</div>
    </n-spin>
  </n-card>
</template>

<style scoped>
.person-empty {
  color: #999;
  font-size: 13px;
  text-align: center;
  padding: 24px 0;
}
</style>
