<script setup lang="ts">
/** 分析详情弹窗：模型信息、提取标签与 AI 原始响应。 */

import { formatMs, formatDate } from '@/utils/format'
import type { AnalysisDetail } from '@/types/analysis'

defineProps<{
  visible: boolean
  loading: boolean
  detail: AnalysisDetail | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

/** 标签分类中文名 */
const tagCategoryLabel: Record<string, string> = {
  style: '风格', item_type: '单品类型', color: '颜色', fit: '版型',
  body_part: '穿着方式', attribute: '属性',
  outfit: '穿搭大标签',
}
</script>

<template>
  <n-modal :show="visible" @update:show="(v: boolean) => emit('update:visible', v)" preset="card" title="分析详情" style="max-width:720px" :mask-closable="true">
    <n-spin :show="loading">
      <template v-if="detail">
        <n-descriptions label-placement="left" :column="2" size="small" bordered style="margin-bottom:16px">
          <n-descriptions-item label="模型">{{ detail.model_name }}</n-descriptions-item>
          <n-descriptions-item label="状态">
            <n-tag :type="detail.status === 'success' ? 'success' : 'error'" size="small">
              {{ detail.status === 'success' ? '成功' : '失败' }}
            </n-tag>
          </n-descriptions-item>
          <n-descriptions-item label="耗时">{{ formatMs(detail.processing_time_ms) }}</n-descriptions-item>
          <n-descriptions-item label="时间">{{ formatDate(detail.created_at || '') }}</n-descriptions-item>
          <n-descriptions-item v-if="detail.error" label="错误信息" :span="2">
            <span style="color:red">{{ detail.error }}</span>
          </n-descriptions-item>
        </n-descriptions>

        <div v-if="detail.tags.length > 0">
          <h4 style="margin-bottom:8px">提取的标签</h4>
          <n-space v-for="cat in ['style','item_type','color','fit','body_part','attribute']" :key="cat" style="margin-bottom:8px" align="center">
            <n-tag type="info" size="small" :bordered="false">{{ tagCategoryLabel[cat] || cat }}</n-tag>
            <template v-for="tag in detail.tags.filter(t=>t.category===cat)" :key="tag.name">
              <n-tag size="small" round>
                {{ tag.name }}
                <span style="font-size:11px;color:#999;margin-left:2px">{{ tag.confidence }}</span>
              </n-tag>
            </template>
            <span v-if="!detail.tags.some(t=>t.category===cat)" style="color:#ccc;font-size:12px">—</span>
          </n-space>
        </div>
        <n-empty v-else-if="!detail.error" description="无标签数据" size="small" />

        <n-collapse v-if="detail.raw_response" style="margin-top:16px">
          <n-collapse-item title="AI 原始响应" name="raw">
            <n-code :code="detail.raw_response" language="json" word-wrap />
          </n-collapse-item>
        </n-collapse>
      </template>
    </n-spin>
  </n-modal>
</template>
