<script setup lang="ts">
/** 数据导出面板：导出全部素材为 CSV，供 Excel / 表格工具离线分析。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { exportInspirationsCsv } from '@/api/admin'

const message = useMessage()
const exporting = ref(false)

async function handleExport() {
  exporting.value = true
  try {
    await exportInspirationsCsv()
    message.success('已导出素材 CSV')
  } catch {
    message.error('导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <n-card title="数据导出" size="small">
    <p style="color: #666; font-size: 13px; margin: 0 0 12px">
      导出全部素材（含标签、关联人物、审核状态、来源等）为 CSV 文件，可在 Excel 中打开做离线统计或备份清单。
    </p>
    <n-button type="primary" size="small" :loading="exporting" @click="handleExport">
      导出 CSV
    </n-button>
  </n-card>
</template>
