<script setup lang="ts">
/** 相似标签（重复）面板：展示重复对并提供合并/设别名操作。 */

import type { DuplicatePair } from '@/api/tags'

defineProps<{ pairs: DuplicatePair[] }>()

const emit = defineEmits<{
  close: []
  merge: [a: number, b: number]
  'set-alias': [sourceId: number, targetId: number, sourceName: string]
}>()
</script>

<template>
  <n-card v-if="pairs.length > 0" title="相似标签" size="small" style="margin-bottom:16px;border-color:#f0a020">
    <template #header-extra>
      <n-button size="small" text @click="emit('close')">关闭</n-button>
    </template>
    <n-list>
      <n-list-item v-for="pair in pairs.slice(0, 20)" :key="`${pair.tag_a.id}-${pair.tag_b.id}`">
        <n-space align="center">
          <n-tag size="small">{{ pair.tag_a.name }}</n-tag>
          <span style="font-size:12px;color:#999">相似度 {{ (pair.similarity * 100).toFixed(0) }}%</span>
          <n-tag size="small">{{ pair.tag_b.name }}</n-tag>
          <n-popconfirm @positive-click="emit('merge', pair.tag_a.id, pair.tag_b.id)">
            <template #trigger>
              <n-button size="tiny" type="warning">
                合并 → {{ pair.tag_a.name }}
              </n-button>
            </template>
            确认合并？源标签「{{ pair.tag_b.name }}」将被删除，其关联素材会迁移到「{{ pair.tag_a.name }}」
          </n-popconfirm>
          <n-popconfirm @positive-click="emit('merge', pair.tag_b.id, pair.tag_a.id)">
            <template #trigger>
              <n-button size="tiny" type="warning">
                合并 → {{ pair.tag_b.name }}
              </n-button>
            </template>
            确认合并？源标签「{{ pair.tag_a.name }}」将被删除，其关联素材会迁移到「{{ pair.tag_b.name }}」
          </n-popconfirm>
          <n-popconfirm @positive-click="emit('set-alias', pair.tag_b.id, pair.tag_a.id, pair.tag_b.name)">
            <template #trigger>
              <n-button size="tiny" type="info">
                设别名 → {{ pair.tag_a.name }}
              </n-button>
            </template>
            确认将「{{ pair.tag_b.name }}」合并到「{{ pair.tag_a.name }}」并设为其别名？此后 AI 再识别出「{{ pair.tag_b.name }}」将自动归为「{{ pair.tag_a.name }}」
          </n-popconfirm>
        </n-space>
      </n-list-item>
    </n-list>
  </n-card>
</template>
