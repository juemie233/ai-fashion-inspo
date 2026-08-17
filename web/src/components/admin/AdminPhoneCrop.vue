<script setup lang="ts">
/** 手机图剪裁面板：扫描候选（只读预览）→ 手动勾选确认 → 执行裁剪 → 自动入队向量回填。 */

import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import axios from 'axios'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()
const router = useRouter()

/** 从请求异常中提取后端 detail 文案（非 Axios 错误返回空串） */
function errorDetail(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: string } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

/** 裁剪模式：auto 黑边自动检测 / ratio 固定比例 */
const mode = ref<'auto' | 'ratio'>('auto')
/** 顶部/底部裁剪比例（百分比，仅 ratio 模式生效） */
const cropTop = ref(3)
const cropBottom = ref(5)
/** 单次最多返回候选数 */
const limit = ref(200)

const scanning = ref(false)
const cropping = ref(false)

/** 扫描候选 */
interface CropCandidate {
  id: string
  file_path: string
  width: number
  height: number
  ratio: number
  crop_top: number
  crop_bottom: number
  auto_ok: boolean
  note: string | null
  confidence: 'high' | 'medium' | 'low'
  created_at: string | null
}

/** 置信度展示文案与标签类型 */
const CONFIDENCE_LABELS: Record<string, { text: string; type: 'success' | 'warning' | 'default' }> =
  {
    high: { text: '高置信', type: 'success' },
    medium: { text: '中置信', type: 'warning' },
    low: { text: '低置信', type: 'default' },
  }

/** 候选网格与勾选状态 */
const candidates = ref<CropCandidate[]>([])
const checkedIds = ref<Set<string>>(new Set())

/** 执行结果 */
interface CropApplyResult {
  processed: number
  skipped: Array<{
    id: string
    reason: string
    file_path?: string | null
    thumbnail_path?: string | null
    created_at?: string | null
  }>
  backup_dir: string | null
  vector_task_id: number | null
}
const result = ref<CropApplyResult | null>(null)

/** 跳过明细折叠面板：全部跳过时默认展开，便于立即查看原因并逐条定位 */
const skippedExpanded = ref<string[]>([])
watch(result, (r) => {
  skippedExpanded.value = r && r.processed === 0 && r.skipped.length > 0 ? ['skipped'] : []
})

/** 跳过素材缩略图（缩略图缺失时回退原图） */
function skipThumbUrl(s: CropApplyResult['skipped'][number]): string {
  return getFileUrl(s.thumbnail_path || s.file_path || '')
}

/** 在素材库中定位单条被跳过的素材（列表仅展示该素材并高亮） */
function locateSkipped(s: CropApplyResult['skipped'][number]) {
  router.push({ path: '/', query: { focus: s.id } })
}

/** 在素材库中定位全部被跳过的素材 */
function locateAllSkipped() {
  if (!result.value) return
  const ids = result.value.skipped.map((s) => s.id).join(',')
  router.push({ path: '/', query: { focus: ids } })
}

/** 大图预览 */
const previewOpen = ref(false)
const previewUrl = ref('')

/** 打开大图预览 */
function openPreview(url: string) {
  previewUrl.value = url
  previewOpen.value = true
}

/** 关闭大图预览 */
function closePreview() {
  previewOpen.value = false
  previewUrl.value = ''
}

const scannedTotal = ref(0)
const checkedCount = computed(() => checkedIds.value.size)

/** 是否只勾选高置信候选（默认勾选 high+medium，排除 low） */
function defaultCheckedIds(items: CropCandidate[]): Set<string> {
  const ids = new Set<string>()
  for (const c of items) {
    if (c.auto_ok && c.confidence !== 'low') ids.add(c.id)
  }
  return ids
}

/** 扫描候选：只读预览，不修改任何数据 */
async function handleScan() {
  scanning.value = true
  result.value = null
  try {
    const { data } = await apiClient.post<{ total: number; items: CropCandidate[] }>(
      '/admin/crop-phone-screenshots/scan',
      {
        mode: mode.value,
        crop_top: cropTop.value / 100,
        crop_bottom: cropBottom.value / 100,
        limit: limit.value,
      },
    )
    scannedTotal.value = data.total
    candidates.value = data.items
    checkedIds.value = defaultCheckedIds(data.items)
    if (data.items.length === 0) {
      message.info(
        data.total === 0 ? '没有可裁剪的竖屏截图素材' : `候选超过上限，仅显示前 ${limit.value} 张`,
      )
    }
  } catch (e: unknown) {
    message.error(errorDetail(e) || '扫描失败')
  } finally {
    scanning.value = false
  }
}

/** 切换单个候选勾选 */
function toggleCheck(id: string) {
  const next = new Set(checkedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checkedIds.value = next
}

/** 全选 / 取消全选（只作用于 auto 检测成功的条目） */
function toggleAll() {
  if (checkedCount.value === candidates.value.length) {
    checkedIds.value = new Set()
  } else {
    checkedIds.value = defaultCheckedIds(candidates.value)
  }
}

/** 确认裁剪：仅处理勾选的素材，成功后自动入队向量回填 */
async function handleApply() {
  if (checkedCount.value === 0) {
    message.warning('请先勾选要裁剪的素材')
    return
  }
  cropping.value = true
  result.value = null
  try {
    const { data } = await apiClient.post<CropApplyResult>('/admin/crop-phone-screenshots/apply', {
      ids: [...checkedIds.value],
      mode: mode.value,
      crop_top: cropTop.value / 100,
      crop_bottom: cropBottom.value / 100,
    })
    result.value = data
    if (data.processed > 0) {
      message.success(`裁剪完成：成功 ${data.processed} 张，已入队向量回填`)
      // 裁剪成功后刷新候选：已处理的素材不再出现在候选列表
      await handleScan()
    } else if (data.skipped.length > 0) {
      message.warning(
        `裁剪完成：成功 0 张（${data.skipped.length} 张跳过），可在下方跳过明细中逐条「定位」`,
      )
    }
  } catch (e: unknown) {
    message.error(errorDetail(e) || '裁剪失败')
  } finally {
    cropping.value = false
  }
}

/** 候选缩略图地址（缩略图可能存在缺失，回退原图） */
function thumbUrl(c: CropCandidate): string {
  return getFileUrl(c.file_path)
}

/** 裁剪比例展示文案 */
function cropLabel(c: CropCandidate): string {
  return `${(c.crop_top * 100).toFixed(1)}% / ${(c.crop_bottom * 100).toFixed(1)}%`
}

/** 上传时间展示：MM-DD HH:mm */
function timeLabel(c: { created_at: string | null }): string {
  if (!c.created_at) return ''
  const t = c.created_at.replace('T', ' ').slice(0, 16)
  return t.slice(5)
}
</script>

<template>
  <n-card title="手机图剪裁" size="small" style="margin-bottom: 24px">
    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      扫描手动上传素材中的手机全屏截图（仅「手动上传 + 竖屏 高/宽 ≥ 1.75」），
      <b>人工勾选确认后</b>执行裁剪：裁掉顶部状态栏、底部导航栏等多余区域。 原图自动备份到
      <code>storage/_crop_backup/</code>，裁剪成功后自动入队向量回填；
      标签/收藏等信息不动。候选按上传时间倒序排列，点击缩略图可查看大图。
    </p>

    <n-form label-placement="left" label-width="110" size="small" style="max-width: 560px">
      <n-form-item label="裁剪模式">
        <n-radio-group v-model:value="mode">
          <n-radio-button value="auto">自动检测黑边（推荐）</n-radio-button>
          <n-radio-button value="ratio">固定比例</n-radio-button>
        </n-radio-group>
      </n-form-item>

      <template v-if="mode === 'ratio'">
        <n-form-item label="顶部裁剪">
          <n-input-number v-model:value="cropTop" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </n-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999">默认 3%（状态栏区域）</span>
        </n-form-item>
        <n-form-item label="底部裁剪">
          <n-input-number v-model:value="cropBottom" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </n-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999"
            >默认 5%（底部导航栏/手势条）</span
          >
        </n-form-item>
      </template>

      <n-form-item label="数量上限">
        <n-input-number v-model:value="limit" :min="1" :max="1000" style="width: 120px" />
        <span style="margin-left: 8px; font-size: 12px; color: #999">单次最多扫描的候选数</span>
      </n-form-item>

      <n-form-item label=" ">
        <n-button type="primary" :loading="scanning" @click="handleScan">
          {{ scanning ? '扫描中...' : '扫描候选' }}
        </n-button>
        <span style="margin-left: 12px; font-size: 12px; color: #999">
          默认勾选高/中置信候选，低置信需人工复核
        </span>
      </n-form-item>
    </n-form>

    <!-- 候选网格：人工勾选确认 -->
    <template v-if="candidates.length > 0">
      <n-divider style="margin: 12px 0" />
      <div
        style="display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap"
      >
        <n-checkbox
          :checked="checkedCount > 0 && checkedCount === candidates.length"
          :indeterminate="checkedCount > 0 && checkedCount < candidates.length"
          @update:checked="toggleAll"
        />
        <span style="font-size: 13px">
          已勾选 <b>{{ checkedCount }}</b> / {{ candidates.length }} 张（共扫描
          {{ scannedTotal }} 张候选）
        </span>
        <n-button
          size="small"
          type="primary"
          :loading="cropping"
          :disabled="checkedCount === 0"
          @click="handleApply"
        >
          {{ cropping ? '裁剪中...' : `确认裁剪（${checkedCount} 张）` }}
        </n-button>
      </div>

      <div class="crop-grid">
        <div
          v-for="c in candidates"
          :key="c.id"
          class="crop-item"
          :class="{ checked: checkedIds.has(c.id), failed: !c.auto_ok }"
          @click="toggleCheck(c.id)"
          @dblclick="previewUrl = thumbUrl(c)"
        >
          <img
            :src="thumbUrl(c)"
            :alt="c.id"
            loading="lazy"
            @click.stop="openPreview(thumbUrl(c))"
          />
          <div class="crop-meta">
            <span class="crop-line">
              {{ timeLabel(c) }} · {{ c.width }}×{{ c.height }} · {{ c.ratio }}
            </span>
            <span class="crop-line">
              裁剪 {{ cropLabel(c) }}
              <n-tag
                size="tiny"
                :bordered="false"
                :type="CONFIDENCE_LABELS[c.confidence]?.type || 'default'"
                style="margin-left: 4px"
              >
                {{ CONFIDENCE_LABELS[c.confidence]?.text || c.confidence }}
              </n-tag>
            </span>
            <n-tag v-if="!c.auto_ok" size="tiny" type="error" :bordered="false">{{ c.note }}</n-tag>
          </div>
          <div class="crop-check" :class="{ checked: checkedIds.has(c.id) }">
            <span v-if="checkedIds.has(c.id)">✓</span>
          </div>
        </div>
      </div>
      <p style="font-size: 12px; color: #999; margin-top: 8px">
        点击缩略图查看大图，双击卡片切换勾选
      </p>
    </template>

    <!-- 执行结果 -->
    <template v-if="result">
      <n-divider style="margin: 12px 0" />
      <n-alert :type="result.processed > 0 ? 'success' : 'warning'" style="margin-bottom: 8px">
        成功裁剪 {{ result.processed }} 张 · 跳过 {{ result.skipped.length }} 张
        <template v-if="result.vector_task_id">
          · 已入队向量回填任务 #{{ result.vector_task_id }}（worker 执行）
        </template>
        <template v-if="result.backup_dir">
          · 原图备份：<code>{{ result.backup_dir }}</code>
        </template>
      </n-alert>

      <n-collapse
        v-if="result.skipped.length > 0"
        v-model:expanded-names="skippedExpanded"
        style="margin-top: 8px"
      >
        <n-collapse-item title="跳过明细" name="skipped">
          <template #header>
            <span>跳过明细（{{ result.skipped.length }} 张）· 点击「定位」在素材库中精确跳转</span>
          </template>
          <div v-if="result.skipped.length > 1" style="margin-bottom: 8px">
            <n-button size="tiny" type="primary" @click="locateAllSkipped">
              全部在素材库中定位（{{ result.skipped.length }} 张）
            </n-button>
          </div>
          <ul class="skip-list">
            <li v-for="s in result.skipped" :key="s.id" class="skip-item">
              <img v-if="s.file_path" class="skip-thumb" :src="skipThumbUrl(s)" :alt="s.id" />
              <div class="skip-info">
                <div class="skip-reason">{{ s.reason }}</div>
                <div class="skip-meta">
                  {{ s.id.slice(0, 8) }}…<template v-if="s.created_at">
                    · {{ timeLabel({ created_at: s.created_at }) }}</template
                  >
                </div>
              </div>
              <n-button size="tiny" type="primary" quaternary @click="locateSkipped(s)">
                定位
              </n-button>
            </li>
          </ul>
        </n-collapse-item>
      </n-collapse>
    </template>

    <!-- 大图预览弹窗 -->
    <n-modal
      v-model:show="previewOpen"
      preset="card"
      title="预览原图"
      style="width: 90%; max-width: 420px"
      :bordered="false"
      @close="closePreview"
      @mask-click="closePreview"
    >
      <div
        style="
          max-height: 72vh;
          overflow-y: auto;
          background: #111;
          border-radius: 8px;
          padding: 8px;
        "
      >
        <img v-if="previewUrl" :src="previewUrl" alt="预览" style="width: 100%; display: block" />
      </div>
    </n-modal>
  </n-card>
</template>

<style scoped>
.crop-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 10px;
}

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
