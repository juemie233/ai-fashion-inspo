<script setup lang="ts">
/** 任务漏斗视图弹窗：汇总漏斗 + 每次搜索明细。 */

import { computed } from 'vue'
import type { FunnelDiagnostics } from '@/types/scraper'

const props = defineProps<{
  show: boolean
  taskId: number | null
  data: FunnelDiagnostics | null
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
}>()

function onUpdateShow(v: boolean) {
  emit('update:show', v)
}

function funnelPct(value: number, max: number): string {
  if (!max) return '0%'
  return Math.round(value / max * 100) + '%'
}

const BAR_COLORS = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']
function barColor(idx: number) { return BAR_COLORS[idx % BAR_COLORS.length] }

function sortLabel(s: string): string {
  const m: Record<string, string> = { general: '综合', time_descending: '最新', popularity_descending: '最热' }
  return m[s] || s
}

/** 汇总漏斗各阶段派生值，供模板使用 */
const funnelStages = computed(() => {
  const s = props.data?.summary
  if (!s) return null
  const found = s.total_found || 0
  const deduped = found - (s.skipped_url_seen || 0) - (s.skipped_content_dup || 0)
  const downloaded = deduped - (s.skipped_http_error || 0) - (s.skipped_network_error || 0)
  const unprocessed = Math.max(0, downloaded - (s.total_added || 0))
  return { found, deduped, downloaded, unprocessed, added: s.total_added || 0 }
})
</script>

<template>
<n-modal :show="show" @update:show="onUpdateShow" preset="card" :title="taskId !== null ? '📊 任务 #' + taskId + ' 漏斗视图' : '漏斗视图'" style="max-width:960px">
  <div v-if="taskId !== null && data" class="funnel-panel-content">
    <!-- 汇总漏斗 -->
    <div class="funnel-section">
      <div class="funnel-section-title">📈 任务汇总</div>
      <div class="funnel-bars" v-if="funnelStages">
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">搜索提取</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelStages.found,funnelStages.found),background:barColor(0)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelStages.found }}</span>
          <span class="funnel-bar-pct">100%</span>
        </div>
        <div class="funnel-drop">↓ -{{ data.summary.skipped_url_seen + data.summary.skipped_content_dup }} 已存在/MD5重复</div>
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">去重后</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelStages.deduped,funnelStages.found),background:barColor(1)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelStages.deduped }}</span>
          <span class="funnel-bar-pct">{{ funnelPct(funnelStages.deduped,funnelStages.found) }}</span>
        </div>
        <div class="funnel-drop">↓ -{{ data.summary.skipped_http_error + data.summary.skipped_network_error }} HTTP/网络失败</div>
        <div class="funnel-bar-row">
          <span class="funnel-bar-label">下载成功</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelStages.downloaded,funnelStages.found),background:barColor(2)}"></div>
          </div>
          <span class="funnel-bar-count">{{ funnelStages.downloaded }}</span>
          <span class="funnel-bar-pct">{{ funnelPct(funnelStages.downloaded,funnelStages.found) }}</span>
        </div>
        <div class="funnel-drop" v-if="funnelStages.unprocessed > 0">↓ -{{ funnelStages.unprocessed }} 未处理（已达目标数量）</div>
        <div class="funnel-bar-row funnel-bar-final">
          <span class="funnel-bar-label">★ 最终入库</span>
          <div class="funnel-bar-track">
            <div class="funnel-bar-fill" :style="{width:funnelPct(funnelStages.added,funnelStages.found),background:barColor(3)}"></div>
          </div>
          <span class="funnel-bar-count" style="font-weight:700">{{ funnelStages.added }}</span>
          <span class="funnel-bar-pct" style="font-weight:700">{{ funnelPct(funnelStages.added,funnelStages.found) }}</span>
        </div>
      </div>
    </div>

    <!-- 每次搜索明细 -->
    <div v-if="data.per_search.length" class="funnel-section">
      <div class="funnel-section-title">🔍 每次搜索明细（{{ data.per_search.length }} 次）</div>
      <div v-for="(ps, psi) in data.per_search" :key="psi" class="funnel-per-search">
        <div class="funnel-ps-header">
          <n-tag size="tiny" :bordered="false">#{{ psi + 1 }}</n-tag>
          <strong>{{ ps.keyword }}</strong>
          <span class="funnel-ps-sort">{{ sortLabel(ps.sort_type) }}</span>
          <n-tag v-if="ps.error" type="error" size="tiny">{{ ps.error }}</n-tag>
        </div>
        <div v-if="!ps.error" class="funnel-bars funnel-bars-sm">
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">卡片</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.cards_total!,ps.cards_total!),background:barColor(0)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.cards_total }}</span>
          </div>
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">有图片</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.cards_with_img!,ps.cards_total!),background:barColor(1)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.cards_with_img }}</span>
            <span class="funnel-bar-pct" v-if="ps.cards_total">(-{{ ps.cards_total! - ps.cards_with_img! }} 无图)</span>
          </div>
          <div class="funnel-bar-row">
            <span class="funnel-bar-label">有效URL</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.urls_extracted!,ps.cards_total!),background:barColor(2)}"></div>
            </div>
            <span class="funnel-bar-count">{{ ps.urls_extracted }}</span>
            <span class="funnel-bar-pct" v-if="(ps.skipped_small||0) + (ps.skipped_icon||0) > 0">(-小图{{ps.skipped_small||0}}/图标{{ps.skipped_icon||0}})</span>
          </div>
          <div class="funnel-bar-row funnel-bar-final">
            <span class="funnel-bar-label">入库</span>
            <div class="funnel-bar-track">
              <div class="funnel-bar-fill" :style="{width:funnelPct(ps.batch_added!,ps.cards_total!),background:barColor(3)}"></div>
            </div>
            <span class="funnel-bar-count" style="font-weight:700">{{ ps.batch_added }}</span>
            <span class="funnel-bar-pct" v-if="ps.batch_skipped_existing||ps.batch_skipped_content_dup||ps.batch_skipped_http||ps.batch_skipped_network">(跳过: 已存在{{ps.batch_skipped_existing||0}} MD5{{ps.batch_skipped_content_dup||0}} HTTP{{ps.batch_skipped_http||0}} 网络{{ps.batch_skipped_network||0}})</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</n-modal>
</template>

<style scoped>
.funnel-panel-content{max-height:72vh;overflow-y:auto;padding-right:4px}
.funnel-section{margin-bottom:20px}
.funnel-section-title{font-size:13px;font-weight:600;color:#333;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #f0f0f0}
.funnel-bars{display:flex;flex-direction:column;gap:4px}
.funnel-bars-sm{gap:2px}
.funnel-bar-row{display:flex;align-items:center;gap:8px;font-size:12px}
.funnel-bar-label{width:56px;flex-shrink:0;text-align:right;color:#666;font-size:11px}
.funnel-bar-track{flex:1;height:18px;background:#f5f5f5;border-radius:4px;overflow:hidden;min-width:60px}
.funnel-bar-fill{height:100%;border-radius:4px;transition:width .4s ease;min-width:2px;opacity:.85}
.funnel-bar-count{width:36px;flex-shrink:0;text-align:right;font-variant-numeric:tabular-nums;font-size:12px;color:#333}
.funnel-bar-pct{width:44px;flex-shrink:0;font-size:10px;color:#999;font-variant-numeric:tabular-nums}
.funnel-bar-final{border-top:1px dashed #e0e0e0;padding-top:4px;margin-top:2px}
.funnel-drop{font-size:10px;color:#999;padding-left:64px}
.funnel-per-search{margin-bottom:12px;padding:10px;background:#fafafa;border-radius:6px}
.funnel-ps-header{display:flex;align-items:center;gap:6px;margin-bottom:8px;font-size:13px}
.funnel-ps-sort{font-size:11px;color:#999}
</style>
