<script setup lang="ts">
/** 分析结果对比弹窗：耗时、标签数量、标签差异与历次响应。 */

import { getFileUrl } from '@/api/inspirations'
import { formatMs, formatDate } from '@/utils/format'
import type { CompareData } from '@/types/analysis'

defineProps<{
  visible: boolean
  loading: boolean
  data: CompareData | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()
</script>

<template>
  <a-modal
    :visible="visible"
    @update:visible="(v: boolean) => emit('update:visible', v)"
    title="分析结果对比"
    :width="960"
    :footer="false"
    :modal-style="{ maxWidth: '960px' }"
    :mask-closable="true"
  >
    <a-spin :loading="loading">
      <template v-if="data">
        <div v-if="data.thumbnail_path" style="text-align: center; margin-bottom: 16px">
          <img
            :src="getFileUrl(data.thumbnail_path)"
            style="max-height: 200px; border-radius: 8px"
          />
        </div>

        <a-card title="⏱ 耗时对比" size="small" style="margin-bottom: 12px">
          <div style="display: flex; gap: 12px; flex-wrap: wrap">
            <div
              v-for="tc in data.time_comparison"
              :key="tc.analysis_id"
              style="
                flex: 1;
                min-width: 140px;
                text-align: center;
                padding: 8px;
                background: #f5f5f5;
                border-radius: 6px;
              "
            >
              <div style="font-weight: 600; font-size: 13px">{{ tc.model_name }}</div>
              <div style="font-size: 11px; color: #999">{{ formatDate(tc.created_at) }}</div>
              <a-tag
                :color="tc.processing_time_ms ? 'green' : 'gray'"
                size="small"
                style="margin-top: 4px"
              >
                {{ formatMs(tc.processing_time_ms) }}
              </a-tag>
            </div>
          </div>
        </a-card>

        <a-card title="📊 标签数量对比" size="small" style="margin-bottom: 12px">
          <div style="display: flex; gap: 12px; flex-wrap: wrap">
            <div
              v-for="a in data.analyses"
              :key="'count-' + a.id"
              style="
                flex: 1;
                min-width: 120px;
                text-align: center;
                padding: 8px;
                background: #f5f5f5;
                border-radius: 6px;
              "
            >
              <div style="font-size: 11px; color: #999">{{ a.model_name }}</div>
              <div
                v-for="(count, cat) in a.tags_count"
                :key="cat"
                style="font-size: 12px; margin: 2px 0"
              >
                <a-tag size="small">{{ cat }}</a-tag> {{ count }}
              </div>
            </div>
          </div>
        </a-card>

        <a-card
          v-if="data.tag_diff"
          title="🔄 标签差异（首次 → 末次）"
          size="small"
          style="margin-bottom: 12px"
        >
          <div v-if="data.tag_diff.added.length" style="margin-bottom: 8px">
            <span style="color: #18a058; font-weight: 600; font-size: 12px"
              >+ 新增 ({{ data.tag_diff.added.length }}):</span
            >
            <a-tag
              v-for="t in data.tag_diff.added"
              :key="'a-' + t"
              size="small"
              color="green"
              style="margin: 1px"
              >{{ t }}</a-tag
            >
          </div>
          <div v-if="data.tag_diff.removed.length" style="margin-bottom: 8px">
            <span style="color: #d03050; font-weight: 600; font-size: 12px"
              >− 消失 ({{ data.tag_diff.removed.length }}):</span
            >
            <a-tag
              v-for="t in data.tag_diff.removed"
              :key="'r-' + t"
              size="small"
              color="red"
              style="margin: 1px"
              >{{ t }}</a-tag
            >
          </div>
          <div v-if="data.tag_diff.common.length">
            <span style="color: #2080f0; font-weight: 600; font-size: 12px"
              >= 共同 ({{ data.tag_diff.common.length }}):</span
            >
            <a-tag
              v-for="t in data.tag_diff.common.slice(0, 20)"
              :key="'c-' + t"
              size="small"
              color="arcoblue"
              style="margin: 1px"
              >{{ t }}</a-tag
            >
            <span v-if="data.tag_diff.common.length > 20" style="font-size: 11px; color: #999">
              ...还有 {{ data.tag_diff.common.length - 20 }} 个</span
            >
          </div>
        </a-card>

        <a-collapse>
          <a-collapse-item
            v-for="(a, idx) in data.analyses"
            :key="a.id"
            :header="`#${idx + 1} — ${a.model_name} — ${a.status === 'success' ? '✓' : '✗'} — ${formatDate(a.created_at)}`"
          >
            <div v-if="a.error" style="color: #d03050; font-size: 13px; margin-bottom: 8px">
              {{ a.error }}
            </div>
            <div v-if="a.parsed_response" style="font-size: 12px">
              <div v-for="(val, key) in a.parsed_response" :key="key" style="margin: 4px 0">
                <a-tag color="arcoblue" size="small">{{ key }}</a-tag>
                <code style="margin-left: 4px; word-break: break-all">{{
                  JSON.stringify(val)
                }}</code>
              </div>
            </div>
          </a-collapse-item>
        </a-collapse>

        <a-empty
          v-if="data.analyses.length < 2"
          description="只有一次分析记录，无法对比"
          style="margin-top: 16px"
        />
      </template>
    </a-spin>
  </a-modal>
</template>
