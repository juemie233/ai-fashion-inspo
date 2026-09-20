<script setup lang="ts">
/** 手机图剪裁「扫描结果区」：候选网格（人工勾选确认）+ 执行结果 + 跳过明细。
 *
 * 从 AdminPhoneCrop 模板原样抽出（提取式重构，标记与判定逻辑不变）；扫描/裁剪/勾选
 * 状态仍由父组件持有，这里只做展示与事件上抛。
 */

import { ref, watch } from 'vue'
import { getFileUrl } from '@/api/inspirations'
import type { CropApplyResult } from '@/utils/cropResult'
import type { CropCandidate, CropGridDensity } from '@/types/crop'
import DensityImageGrid from '@/components/common/DensityImageGrid.vue'

const props = defineProps<{
  /** 扫描候选 */
  candidates: CropCandidate[]
  /** 勾选集合 */
  checkedIds: Set<string>
  /** 已勾选数量 */
  checkedCount: number
  /** 本次会话累计扫描的素材数 */
  scannedTotal: number
  /** AI 复核命中数（阳性候选已置顶） */
  vlmHits: number
  /** 裁剪执行中 */
  cropping: boolean
  /** 执行结果（未执行过为 null） */
  result: CropApplyResult | null
}>()

const emit = defineEmits<{
  (e: 'toggleCheck', id: string): void
  (e: 'toggleAll', checked: unknown): void
  (e: 'apply'): void
  (e: 'openPreview', url: string, id: string): void
  (e: 'locate', focus: string): void
}>()

/** 候选网格密度（原父组件状态，经 v-model 双向绑定） */
const gridDensity = defineModel<CropGridDensity>('gridDensity', { required: true })

/** content 模式检测类型展示文案 */
const BOUNDARY_LABELS: Record<string, string> = {
  gray_band: '灰带包夹',
  status_bar: '状态栏+播放器条',
  plain: '内容边界',
}

/** 置信度展示文案与标签颜色（Arco Tag 使用 color 预设色，无 type 语义色） */
const CONFIDENCE_LABELS: Record<string, { text: string; color: 'green' | 'orange' | 'gray' }> = {
  high: { text: '高置信', color: 'green' },
  medium: { text: '中置信', color: 'orange' },
  low: { text: '低置信', color: 'gray' },
}

/** 候选缩略图地址（缩略图可能存在缺失，回退原图） */
function thumbUrl(c: CropCandidate): string {
  return getFileUrl(c.file_path)
}

/** 裁剪比例展示文案 */
function cropLabel(c: CropCandidate): string {
  return `${(c.crop_top * 100).toFixed(1)}% / ${(c.crop_bottom * 100).toFixed(1)}%`
}

/** 跳过明细折叠面板：全部跳过时默认展开，便于立即查看原因并逐条定位 */
const skippedExpanded = ref<string[]>([])
watch(
  () => props.result,
  (r) => {
    skippedExpanded.value = r && r.processed === 0 && r.skipped.length > 0 ? ['skipped'] : []
  },
)

/** 跳过素材缩略图（缩略图缺失时回退原图） */
function skipThumbUrl(s: CropApplyResult['skipped'][number]): string {
  return getFileUrl(s.thumbnail_path || s.file_path || '')
}

/** 在素材库中定位单条被跳过的素材（列表仅展示该素材并高亮） */
function locateSkipped(s: CropApplyResult['skipped'][number]) {
  emit('locate', s.id)
}

/** 在素材库中定位全部被跳过的素材 */
function locateAllSkipped() {
  if (!props.result) return
  emit('locate', props.result.skipped.map((s) => s.id).join(','))
}
</script>

<template>
  <!-- 候选网格：人工勾选确认 -->
  <template v-if="candidates.length > 0">
    <a-divider style="margin: 12px 0" />
    <!-- 候选网格：通用密度网格组件，紧凑/标准/宽松可调，默认标准 -->
    <DensityImageGrid v-model:density="gridDensity">
      <template #header-left>
        <a-checkbox
          :model-value="checkedCount > 0 && checkedCount === candidates.length"
          :indeterminate="checkedCount > 0 && checkedCount < candidates.length"
          @change="(v: unknown) => emit('toggleAll', v)"
        />
        <span style="font-size: 13px">
          已勾选 <b>{{ checkedCount }}</b> / {{ candidates.length }} 张（共扫描
          {{ scannedTotal }} 张候选）
        </span>
        <a-tag v-if="vlmHits > 0" size="small" color="green" :bordered="false">
          AI 复核命中 {{ vlmHits }} 张（已置顶）
        </a-tag>
        <a-button
          size="small"
          type="primary"
          :loading="cropping"
          :disabled="checkedCount === 0"
          @click="emit('apply')"
        >
          {{ cropping ? '裁剪中...' : `确认裁剪（${checkedCount} 张）` }}
        </a-button>
      </template>

      <div
        v-for="c in candidates"
        :key="c.id"
        class="crop-item"
        :class="[{ checked: checkedIds.has(c.id), failed: !c.auto_ok }, 'density-' + gridDensity]"
        @click="emit('toggleCheck', c.id)"
        @dblclick="emit('openPreview', thumbUrl(c), c.id)"
      >
        <img
          :src="thumbUrl(c)"
          :alt="c.id"
          loading="lazy"
          @click.stop="emit('openPreview', thumbUrl(c), c.id)"
        />
        <div class="crop-meta">
          <span class="crop-line"> {{ c.width }}×{{ c.height }} · {{ c.ratio }} </span>
          <span class="crop-line">
            裁剪 {{ cropLabel(c) }}
            <a-tag
              v-if="c.boundary_kind"
              size="small"
              :bordered="false"
              color="arcoblue"
              style="margin-left: 4px"
            >
              {{ BOUNDARY_LABELS[c.boundary_kind] || c.boundary_kind }}
            </a-tag>
            <a-tag
              size="small"
              :bordered="false"
              :color="CONFIDENCE_LABELS[c.confidence]?.color || 'gray'"
              style="margin-left: 4px"
            >
              {{ CONFIDENCE_LABELS[c.confidence]?.text || c.confidence }}
            </a-tag>
            <a-tag
              v-if="c.vlm_residue"
              size="small"
              color="green"
              :bordered="false"
              style="margin-left: 4px"
            >
              AI 复核：检出 UI 残留
            </a-tag>
            <a-tag
              v-else-if="c.vlm_residue === null"
              size="small"
              color="orange"
              :bordered="false"
              style="margin-left: 4px"
            >
              AI 复核不可用
            </a-tag>
          </span>
          <a-tag v-if="!c.auto_ok" size="small" color="red" :bordered="false">{{ c.note }}</a-tag>
        </div>
        <div class="crop-check" :class="{ checked: checkedIds.has(c.id) }">
          <span v-if="checkedIds.has(c.id)">✓</span>
        </div>
      </div>
    </DensityImageGrid>
    <p style="font-size: 12px; color: #999; margin-top: 8px">
      点击缩略图查看大图，双击卡片切换勾选
    </p>
  </template>

  <!-- 执行结果 -->
  <template v-if="result">
    <a-divider style="margin: 12px 0" />
    <a-alert :type="result.processed > 0 ? 'success' : 'warning'" style="margin-bottom: 8px">
      成功裁剪 {{ result.processed }} 张 · 跳过 {{ result.skipped.length }} 张
      <template v-if="result.duplicates.length > 0">
        · 内容重复 {{ result.duplicates.length }} 组待处理
      </template>
      <template v-if="result.vector_task_id">
        · 已入队向量回填任务 #{{ result.vector_task_id }}（worker 执行）
      </template>
      <template v-if="result.backup_dir">
        · 原图备份：<code>{{ result.backup_dir }}</code>
      </template>
    </a-alert>

    <a-collapse
      v-if="result.skipped.length > 0"
      v-model:active-key="skippedExpanded"
      style="margin-top: 8px"
    >
      <a-collapse-item key="skipped">
        <template #header>
          <span>跳过明细（{{ result.skipped.length }} 张）· 点击「定位」在素材库中精确跳转</span>
        </template>
        <div v-if="result.skipped.length > 1" style="margin-bottom: 8px">
          <a-button size="mini" type="primary" @click="locateAllSkipped">
            全部在素材库中定位（{{ result.skipped.length }} 张）
          </a-button>
        </div>
        <ul class="skip-list">
          <li v-for="s in result.skipped" :key="s.id" class="skip-item">
            <img v-if="s.file_path" class="skip-thumb" :src="skipThumbUrl(s)" :alt="s.id" />
            <div class="skip-info">
              <div class="skip-reason">{{ s.reason }}</div>
              <div class="skip-meta">{{ s.id.slice(0, 8) }}…</div>
            </div>
            <a-button size="mini" type="text" @click="locateSkipped(s)">定位</a-button>
          </li>
        </ul>
      </a-collapse-item>
    </a-collapse>
  </template>
</template>

<style scoped>
/* 结果区样式随标记从 AdminPhoneCrop 迁入：scoped 样式不跨组件生效，留在父组件会失效。
   网格布局（列数/间距/密度切换）由通用组件 DensityImageGrid 承担。 */
.crop-item {
  position: relative;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  transition:
    border-color 0.15s,
    opacity 0.15s;
  background: #222; /* 深色底：细长图 contain 显示时观感统一 */
}

.crop-item img {
  width: 100%;
  height: 170px;
  object-fit: contain; /* 完整显示细长截图，而非裁切中间一条 */
  display: block;
  background: #222;
}

/* 图片高度随密度缩放，与列宽变化匹配（紧凑更小、宽松更大） */
.crop-item.density-compact img {
  height: 130px;
}

.crop-item.density-comfortable img {
  height: 210px;
}

.crop-item.checked {
  border-color: #18a058;
}

.crop-item.failed {
  opacity: 0.6;
}

.crop-meta {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 4px 6px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  line-height: 1.6;
  display: flex;
  flex-direction: column;
}

.crop-line {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.crop-check {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  border: 2px solid #ccc;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #fff;
}

.crop-check.checked {
  background: #18a058;
  border-color: #18a058;
}

/* 跳过明细：缩略图 + 原因 + 定位按钮 */
.skip-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skip-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #fafafa;
}

.skip-thumb {
  width: 36px;
  height: 48px;
  object-fit: cover;
  border-radius: 4px;
  background: #eee;
  flex-shrink: 0;
}

.skip-info {
  flex: 1;
  min-width: 0;
}

.skip-reason {
  font-size: 12px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skip-meta {
  font-size: 11px;
  color: #999;
}
</style>
