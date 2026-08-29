<script setup lang="ts">
/** 分析记录批量对比弹窗：勾选同一素材的多条记录（不同模型/提示词组合）并排对比。 */
import { computed } from 'vue'
import { getFileUrl } from '@/api/inspirations'
import { formatMs, formatDate } from '@/utils/format'
import type { CompareBatchData, CompareBatchRecord } from '@/types/analysis'

const props = defineProps<{
  visible: boolean
  loading: boolean
  data: CompareBatchData | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

/** 全部成功记录共有的标签集合 */
const commonTagSet = computed(() => new Set(props.data?.tag_diff?.common ?? []))

/** 判断某条记录的某个标签是否为差异标签（非全体共有） */
function isDifferingTag(record: CompareBatchRecord, tagName: string): boolean {
  if (record.status !== 'success') return false
  return !commonTagSet.value.has(tagName)
}

/** 差异标签出现的记录序号描述（如「记录 1、3」） */
function differingWhere(name: string): string {
  const diff = props.data?.tag_diff?.differing.find((d) => d.name === name)
  if (!diff) return ''
  return diff.log_ids
    .map((id) => props.data!.analyses.findIndex((a) => a.id === id) + 1)
    .filter((n) => n > 0)
    .join('、')
}

/** 记录列头副标题：模型 + 提示词版本 */
function recordSubtitle(record: CompareBatchRecord): string {
  const parts = [record.model_name]
  if (record.prompt_version) parts.push(`Prompt ${record.prompt_version}`)
  return parts.join(' · ')
}
</script>

<template>
  <a-modal
    :visible="visible"
    @update:visible="(v: boolean) => emit('update:visible', v)"
    title="分析记录对比（按勾选记录）"
    :width="1080"
    :footer="false"
    :modal-style="{ maxWidth: '1080px' }"
    :mask-closable="true"
  >
    <a-spin :loading="loading">
      <template v-if="data">
        <div v-if="data.thumbnail_path" style="text-align: center; margin-bottom: 16px">
          <img
            :src="getFileUrl(data.thumbnail_path)"
            style="max-height: 180px; border-radius: 8px"
          />
        </div>

        <a-card title="📋 基本信息" size="small" style="margin-bottom: 12px">
          <div style="display: flex; gap: 12px; flex-wrap: wrap">
            <div
              v-for="(a, idx) in data.analyses"
              :key="'info-' + a.id"
              style="
                flex: 1;
                min-width: 200px;
                padding: 8px 10px;
                background: #f5f5f5;
                border-radius: 6px;
                font-size: 12px;
              "
            >
              <div style="font-weight: 600; margin-bottom: 2px">记录 {{ idx + 1 }}</div>
              <div>{{ recordSubtitle(a) }}</div>
              <div style="color: #86909c">{{ formatDate(a.created_at) }}</div>
              <div style="margin-top: 4px">
                耗时
                <a-tag :color="a.processing_time_ms ? 'green' : 'gray'" size="small">
                  {{ formatMs(a.processing_time_ms) }}
                </a-tag>
                <a-tag :color="a.status === 'success' ? 'green' : 'red'" size="small">
                  {{ a.status === 'success' ? '成功' : '失败' }}
                </a-tag>
              </div>
              <div v-if="a.error" style="color: #d03050; margin-top: 4px; word-break: break-all">
                {{ a.error }}
              </div>
            </div>
          </div>
        </a-card>

        <a-card title="🔀 标签差异（并排对比）" size="small" style="margin-bottom: 12px">
          <template #extra>
            <span style="font-size: 12px; color: #86909c">
              <a-tag color="arcoblue" size="small">共有标签</a-tag>
              <a-tag color="orangered" size="small">差异标签</a-tag>
            </span>
          </template>
          <div style="display: flex; gap: 12px">
            <div
              v-for="(a, idx) in data.analyses"
              :key="'tags-' + a.id"
              style="flex: 1; min-width: 220px"
            >
              <div class="record-col-title">记录 {{ idx + 1 }}（{{ a.tags.length }} 个标签）</div>
              <a-empty v-if="!a.tags.length" description="无标签" />
              <div v-else style="display: flex; flex-wrap: wrap; gap: 2px">
                <a-tooltip
                  v-for="t in a.tags"
                  :key="t.name"
                  :content="`类别: ${t.category} · 置信度: ${t.confidence}`"
                >
                  <a-tag
                    size="small"
                    :color="isDifferingTag(a, t.name) ? 'orangered' : 'arcoblue'"
                    style="margin: 1px"
                  >
                    {{ t.name }}
                  </a-tag>
                </a-tooltip>
              </div>
            </div>
          </div>
        </a-card>

        <a-card v-if="data.tag_diff" title="🧩 差异汇总" size="small">
          <div v-if="data.tag_diff.common.length" style="margin-bottom: 8px">
            <span style="color: #2080f0; font-weight: 600; font-size: 12px">
              = 共有 ({{ data.tag_diff.common.length }}):
            </span>
            <a-tag
              v-for="t in data.tag_diff.common"
              :key="'c-' + t"
              size="small"
              color="arcoblue"
              style="margin: 1px"
            >
              {{ t }}
            </a-tag>
          </div>
          <div v-if="data.tag_diff.differing.length">
            <span style="color: #ff7d00; font-weight: 600; font-size: 12px">
              ≠ 差异 ({{ data.tag_diff.differing.length }}):
            </span>
            <span
              v-for="d in data.tag_diff.differing"
              :key="'d-' + d.name"
              style="margin: 1px; display: inline-flex; align-items: center; gap: 2px"
            >
              <a-tag size="small" color="orangered">{{ d.name }}</a-tag>
              <span style="font-size: 11px; color: #999">记录 {{ differingWhere(d.name) }}</span>
            </span>
          </div>
          <a-empty
            v-if="!data.tag_diff.common.length && !data.tag_diff.differing.length"
            description="各记录之间没有可用标签（可能全部失败）"
          />
        </a-card>
      </template>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.record-col-title {
  font-size: 12px;
  font-weight: 600;
  color: #4e5969;
  margin-bottom: 6px;
}
</style>
