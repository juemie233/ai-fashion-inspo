<script setup lang="ts">
/** CSV 导入结果提示：成功统计 + 失败明细 + 错误告警（仅博主列表展示）。 */

import type { PersonImportResult } from '@shared/types/person'

defineProps<{
  result: PersonImportResult | null
  error: string
}>()

defineEmits<{
  dismiss: []
}>()
</script>

<template>
  <a-alert
    v-if="result"
    :type="result.failed > 0 ? 'warning' : 'success'"
    closable
    style="margin-bottom: 12px"
    @close="$emit('dismiss')"
  >
    <template #title>
      导入完成：新增 {{ result.imported }} 人，更新 {{ result.updated }} 人
      <template v-if="result.skipped > 0">，跳过 {{ result.skipped }} 行（CSV 内重复）</template>
      <template v-if="result.failed > 0">，失败 {{ result.failed }} 行</template>
    </template>
    <div v-if="result.failed > 0" style="max-height: 180px; overflow: auto">
      <div v-for="err in result.errors" :key="err.row" style="font-size: 12px; line-height: 1.8">
        第 {{ err.row }} 行{{ err.nickname ? `（${err.nickname}）` : '' }}：{{ err.reason }}
      </div>
      <a-typography-text
        v-if="result.errors.length < result.failed"
        type="secondary"
        style="font-size: 12px"
      >
        … 共 {{ result.failed }} 行失败，仅展示前 {{ result.errors.length }} 条
      </a-typography-text>
    </div>
  </a-alert>
  <a-alert v-if="error" type="error" closable style="margin-bottom: 12px" @close="$emit('dismiss')">
    {{ error }}
  </a-alert>
</template>
