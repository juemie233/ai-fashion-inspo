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
  style: '风格',
  item_type: '单品类型',
  color: '颜色',
  fit: '版型',
  body_part: '穿着方式',
  attribute: '属性',
  outfit: '穿搭大标签',
  Atmosphere: '氛围',
  Expression: '表情',
  Leg_Posture: '腿部姿态',
}
</script>

<template>
  <a-modal
    :visible="visible"
    @update:visible="(v: boolean) => emit('update:visible', v)"
    title="分析详情"
    :width="720"
    :footer="false"
    :modal-style="{ maxWidth: '720px' }"
    :mask-closable="true"
  >
    <a-spin :loading="loading">
      <template v-if="detail">
        <a-descriptions :column="2" bordered style="margin-bottom: 16px">
          <a-descriptions-item label="模型">{{ detail.model_name }}</a-descriptions-item>
          <a-descriptions-item label="状态">
            <a-tag :color="detail.status === 'success' ? 'green' : 'red'" size="small">
              {{ detail.status === 'success' ? '成功' : '失败' }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="耗时">{{
            formatMs(detail.processing_time_ms)
          }}</a-descriptions-item>
          <a-descriptions-item label="时间">{{
            formatDate(detail.created_at || '')
          }}</a-descriptions-item>
          <a-descriptions-item v-if="detail.error" label="错误信息" :span="2">
            <span style="color: red">{{ detail.error }}</span>
          </a-descriptions-item>
        </a-descriptions>

        <div v-if="detail.tags.length > 0">
          <h4 style="margin-bottom: 8px">提取的标签</h4>
          <a-space
            v-for="cat in [
              'style',
              'item_type',
              'color',
              'fit',
              'body_part',
              'attribute',
              'Atmosphere',
              'Expression',
              'Leg_Posture',
            ]"
            :key="cat"
            style="margin-bottom: 8px"
            align="center"
            wrap
          >
            <a-tag color="arcoblue" size="small">{{ tagCategoryLabel[cat] || cat }}</a-tag>
            <template v-for="tag in detail.tags.filter((t) => t.category === cat)" :key="tag.name">
              <a-tag size="small" style="border-radius: 999px">
                {{ tag.name }}
                <span style="font-size: 11px; color: #999; margin-left: 2px">{{
                  tag.confidence
                }}</span>
              </a-tag>
            </template>
            <span
              v-if="!detail.tags.some((t) => t.category === cat)"
              style="color: #ccc; font-size: 12px"
              >—</span
            >
          </a-space>
        </div>
        <a-empty v-else-if="!detail.error" description="无标签数据" />

        <a-collapse v-if="detail.raw_response" style="margin-top: 16px">
          <a-collapse-item header="AI 原始响应">
            <pre
              style="
                font-size: 12px;
                white-space: pre-wrap;
                word-break: break-all;
                background: #f5f5f5;
                padding: 12px;
                border-radius: 6px;
                margin: 0;
              "
              >{{ detail.raw_response }}</pre>
          </a-collapse-item>
        </a-collapse>
      </template>
    </a-spin>
  </a-modal>
</template>
