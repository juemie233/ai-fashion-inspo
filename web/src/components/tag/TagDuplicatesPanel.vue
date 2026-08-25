<script setup lang="ts">
/** 相似标签（重复）面板：列出重复对，每对提供「图片对比」入口。
 *  合并 / 设别名 / 重命名等操作统一在 TagDuplicateCompareModal 内完成。 */

import type { TagDuplicatePair } from '@/types/tag'

defineProps<{ pairs: TagDuplicatePair[] }>()

const emit = defineEmits<{
  close: []
  compare: [pair: TagDuplicatePair]
}>()
</script>

<template>
  <a-card
    v-if="pairs.length > 0"
    title="相似标签"
    size="small"
    style="margin-bottom: 16px; border-color: #f0a020"
  >
    <template #extra>
      <a-button size="small" type="text" @click="emit('close')">关闭</a-button>
    </template>
    <a-list>
      <a-list-item v-for="pair in pairs.slice(0, 50)" :key="`${pair.tag_a?.id}-${pair.tag_b?.id}`">
        <a-space align="center">
          <a-tag size="small">{{ pair.tag_a?.name }}</a-tag>
          <span style="font-size: 12px; color: #999"
            >相似度 {{ (pair.similarity * 100).toFixed(0) }}%</span
          >
          <a-tag size="small">{{ pair.tag_b?.name }}</a-tag>
          <a-button size="mini" type="primary" @click="emit('compare', pair)">图片对比</a-button>
        </a-space>
      </a-list-item>
    </a-list>
  </a-card>
</template>
