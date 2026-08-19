<script setup lang="ts">
/** 数据导出面板：导出全部素材为 CSV，供 Excel / 表格工具离线分析。 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { exportInspirationsCsv } from '@/api/admin'

const exporting = ref(false)

async function handleExport() {
  exporting.value = true
  try {
    await exportInspirationsCsv()
    Message.success('已导出素材 CSV')
  } catch {
    Message.error('导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <a-card title="数据导出" size="small">
    <p style="color: var(--color-text-2); font-size: 13px; margin: 0 0 12px">
      导出全部素材（含标签、关联人物、审核状态、来源等）为 CSV 文件，可在 Excel 中打开做离线统计或备份清单。
    </p>
    <a-button type="primary" size="small" :loading="exporting" @click="handleExport">
      导出 CSV
    </a-button>
  </a-card>
</template>
