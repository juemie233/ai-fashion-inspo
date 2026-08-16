<script setup lang="ts">
/** 人物内容类型徽标：职业模特 / 穿搭博主的 UI 区分呈现。 */

import { PERSON_TYPE_LABELS, type PersonType } from '@shared/types/person'

defineProps<{
  /** 内容类型：model（职业模特）| blogger（穿搭博主） */
  type: PersonType
  /** 显示大小，默认 small */
  size?: 'small' | 'medium'
}>()
</script>

<template>
  <n-tag
    :type="type === 'model' ? 'info' : 'warning'"
    :size="size || 'small'"
    :bordered="false"
    round
  >
    {{ PERSON_TYPE_LABELS[type] || type }}
  </n-tag>
</template>
