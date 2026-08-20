<script setup lang="ts">
/** 手机图剪裁面板：扫描候选（只读预览）→ 手动勾选确认 → 执行裁剪 → 自动入队向量回填。 */

import { reactive, ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import axios from 'axios'
import apiClient from '@/api/client'
import { getFileUrl, deleteInspiration } from '@/api/inspirations'
import { normalizeApplyResult, type CropApplyResult, type CropDuplicate } from '@/utils/cropResult'

const router = useRouter()

/** 从请求异常中提取后端 detail 文案（非 Axios 错误返回空串） */
function errorDetail(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: string } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

/** 裁剪模式：auto 黑边自动检测 / ratio 固定比例 / content 内容边界检测 */
const mode = ref<'auto' | 'ratio' | 'content'>('auto')
/** 顶部/底部裁剪比例（百分比，仅 ratio 模式生效） */
const cropTop = ref(3)
const cropBottom = ref(5)
/** 单次最多返回候选数 */
const limit = ref(200)

/** a-form 表单模型（Arco 表单要求绑定 model；本面板无校验规则，仅提供上下文） */
const formModel = reactive({ mode, cropTop, cropBottom, limit })

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
  /** content 模式：gray_band（灰带包夹）/ status_bar（状态栏+播放器条）/ plain */
  boundary_kind?: 'gray_band' | 'status_bar' | 'plain' | null
  created_at: string | null
}

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

/** 候选网格与勾选状态 */
const candidates = ref<CropCandidate[]>([])
const checkedIds = ref<Set<string>>(new Set())

/** 执行结果（duplicates/skipped 经 normalizeApplyResult 归一化，恒为数组） */
const result = ref<CropApplyResult | null>(null)

// ── 内容重复对比弹窗：逐组展示「裁剪结果 vs 库中重复素材」，删除权交给用户 ──

/** 待决策的重复组队列 */
const dupQueue = ref<CropDuplicate[]>([])
const showDupModal = ref(false)
const dupProcessing = ref(false)
/** 当前正在决策的重复组（第 1/N 组） */
const currentDup = computed(() => dupQueue.value[0] ?? null)
const dupTotal = computed(() => dupQueue.value.length)

/** 裁剪结果预览 URL（临时文件） */
function dupPreviewUrl(d: CropDuplicate): string {
  return getFileUrl(d.preview_path || '')
}

/** 库中重复素材预览 URL */
function dupTargetUrl(d: CropDuplicate): string {
  return getFileUrl(d.dup_thumbnail_path || d.dup_file_path || '')
}

/** 重复素材上传时间：MM-DD HH:mm */
function dupTimeLabel(d: CropDuplicate): string {
  if (!d.dup_created_at) return ''
  const t = d.dup_created_at.replace('T', ' ').slice(0, 16)
  return t.slice(5)
}

/** 保留裁剪结果：物理删除库中重复素材（不可恢复），然后重新执行本素材的裁剪 */
async function handleDupKeepCrop() {
  const dup = currentDup.value
  if (!dup || dupProcessing.value) return
  dupProcessing.value = true
  try {
    // 物理删除重复素材（用户已在确认弹层中同意，文件/记录/向量一并清除）
    await deleteInspiration(dup.dup_id)
    // 重新执行裁剪：此时应能成功；若仍命中新的重复则继续入队决策
    const { data } = await apiClient.post<CropApplyResult>('/admin/crop-phone-screenshots/apply', {
      ids: [dup.id],
      mode: mode.value,
      crop_top: cropTop.value / 100,
      crop_bottom: cropBottom.value / 100,
    })
    const normalized = normalizeApplyResult(data)
    mergeApplyResult(normalized, dup.id)
    Message.success(
      `已保留裁剪结果（素材 ${dup.id.slice(0, 8)}…），重复素材 ${dup.dup_id.slice(0, 8)}… 已物理删除`,
    )
    if (normalized.duplicates.length > 0) {
      Message.info(`裁剪后又发现 ${normalized.duplicates.length} 组内容重复，请继续对比决策`)
    }
  } catch (e: unknown) {
    Message.error(errorDetail(e) || '处理失败')
  } finally {
    dupProcessing.value = false
    dupQueue.value = dupQueue.value.filter((d) => d.id !== dup.id)
    finishDupQueue()
  }
}

/** 保留原图：本次跳过裁剪（两边都保留），进入下一组对比 */
function handleDupSkip() {
  const dup = currentDup.value
  if (!dup || dupProcessing.value) return
  if (result.value) {
    result.value.duplicates = result.value.duplicates.filter((d) => d.id !== dup.id)
  }
  dupQueue.value = dupQueue.value.filter((d) => d.id !== dup.id)
  Message.info(`已保留原图，跳过裁剪（素材 ${dup.id.slice(0, 8)}…）`)
  finishDupQueue()
}

/** 合并一次重复处理后的 apply 结果到汇总结果 */
function mergeApplyResult(data: CropApplyResult, handledId: string) {
  // 防御：无论调用方是否已归一化，这里再归一化一次保证数组字段安全
  data = normalizeApplyResult(data)
  if (!result.value) {
    result.value = data
  } else {
    const r = result.value
    r.processed += data.processed
    r.skipped = [...r.skipped.filter((s) => s.id !== handledId), ...data.skipped]
    // 新的重复组追加到汇总与待决策队列
    r.duplicates = [...r.duplicates.filter((d) => d.id !== handledId), ...data.duplicates]
    dupQueue.value = [...dupQueue.value.filter((d) => d.id !== handledId), ...data.duplicates]
    if (data.vector_task_id) r.vector_task_id = data.vector_task_id
    if (data.backup_dir) r.backup_dir = data.backup_dir
  }
}

/** 队列处理完毕：关闭弹窗并汇总提示 + 刷新候选 */
function finishDupQueue() {
  if (dupQueue.value.length > 0) return
  showDupModal.value = false
  const r = result.value
  if (!r) return
  if (r.processed > 0) {
    Message.success(`重复对比处理完成：成功裁剪 ${r.processed} 张，已入队向量回填`)
    // 已处理的素材不再出现在候选列表
    handleScan()
  } else if (r.skipped.length > 0 || r.duplicates.length > 0) {
    Message.warning(`裁剪完成：成功 0 张（跳过 ${r.skipped.length} 张）`)
  }
}

/** 关闭弹窗（点击遮罩/关闭按钮）：剩余组按「保留原图」处理 */
function handleDupModalClose() {
  if (dupProcessing.value) return
  dupQueue.value = []
  showDupModal.value = false
  const r = result.value
  if (r && r.processed > 0) handleScan()
}

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
      Message.info(
        data.total === 0 ? '没有可裁剪的竖屏截图素材' : `候选超过上限，仅显示前 ${limit.value} 张`,
      )
    }
  } catch (e: unknown) {
    Message.error(errorDetail(e) || '扫描失败')
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
    Message.warning('请先勾选要裁剪的素材')
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
    // 归一化后再消费：duplicates/skipped 恒为数组，避免字段缺失导致访问崩溃
    result.value = normalizeApplyResult(data)
    console.debug('[手机图剪裁] apply 结果', {
      processed: result.value.processed,
      skipped: result.value.skipped.length,
      duplicates: result.value.duplicates.length,
      vector_task_id: result.value.vector_task_id,
    })
    if (result.value.duplicates.length > 0) {
      // 内容重复：弹出左右对比视图，由用户决定保留哪一张（删除权交给用户）
      dupQueue.value = [...result.value.duplicates]
      showDupModal.value = true
      return
    }
    if (result.value.processed > 0) {
      Message.success(`裁剪完成：成功 ${result.value.processed} 张，已入队向量回填`)
      // 裁剪成功后刷新候选：已处理的素材不再出现在候选列表
      await handleScan()
    } else if (result.value.skipped.length > 0) {
      Message.warning(
        `裁剪完成：成功 0 张（${result.value.skipped.length} 张跳过），可在下方跳过明细中逐条「定位」`,
      )
    }
  } catch (e: unknown) {
    console.error('[手机图剪裁] apply 请求失败', e)
    Message.error(errorDetail(e) || '裁剪失败')
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
  <a-card title="手机图剪裁" size="small" style="margin-bottom: 24px">
    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      扫描手动上传素材中的手机全屏截图（仅「手动上传 + 竖屏 高/宽 ≥ 1.75」），
      <b>人工勾选确认后</b>执行裁剪：裁掉顶部状态栏、底部导航栏等多余区域。 原图自动备份到
      <code>storage/_crop_backup/</code>，裁剪成功后自动入队向量回填；
      标签/收藏等信息不动。候选按上传时间倒序排列，点击缩略图可查看大图。
      <br />三种模式并存：「自动检测黑边」面向深色背景截图；「固定比例」按设定比例裁剪；
      <b>「内容边界检测」</b>面向上下被灰带/状态栏/播放器条包夹的截图，自动定位照片主体边界（100
      张样本分析校准）。
    </p>

    <a-form
      :model="formModel"
      label-align="left"
      :label-col-style="{ width: '110px' }"
      size="small"
      style="max-width: 560px"
    >
      <a-form-item label="裁剪模式">
        <a-radio-group v-model="mode" type="button" size="small">
          <a-radio value="auto">自动检测黑边</a-radio>
          <a-radio value="content">内容边界检测（新）</a-radio>
          <a-radio value="ratio">固定比例</a-radio>
        </a-radio-group>
      </a-form-item>

      <template v-if="mode === 'ratio'">
        <a-form-item label="顶部裁剪">
          <a-input-number v-model="cropTop" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </a-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999">默认 3%（状态栏区域）</span>
        </a-form-item>
        <a-form-item label="底部裁剪">
          <a-input-number v-model="cropBottom" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </a-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999"
            >默认 5%（底部导航栏/手势条）</span
          >
        </a-form-item>
      </template>

      <a-form-item label="数量上限">
        <a-input-number v-model="limit" :min="1" :max="1000" style="width: 120px" />
        <span style="margin-left: 8px; font-size: 12px; color: #999">单次最多扫描的候选数</span>
      </a-form-item>

      <a-form-item label=" ">
        <a-button type="primary" :loading="scanning" @click="handleScan">
          {{ scanning ? '扫描中...' : '扫描候选' }}
        </a-button>
        <span style="margin-left: 12px; font-size: 12px; color: #999">
          默认勾选高/中置信候选，低置信需人工复核
        </span>
      </a-form-item>
    </a-form>

    <!-- 候选网格：人工勾选确认 -->
    <template v-if="candidates.length > 0">
      <a-divider style="margin: 12px 0" />
      <div
        style="display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap"
      >
        <a-checkbox
          :model-value="checkedCount > 0 && checkedCount === candidates.length"
          :indeterminate="checkedCount > 0 && checkedCount < candidates.length"
          @change="toggleAll"
        />
        <span style="font-size: 13px">
          已勾选 <b>{{ checkedCount }}</b> / {{ candidates.length }} 张（共扫描
          {{ scannedTotal }} 张候选）
        </span>
        <a-button
          size="small"
          type="primary"
          :loading="cropping"
          :disabled="checkedCount === 0"
          @click="handleApply"
        >
          {{ cropping ? '裁剪中...' : `确认裁剪（${checkedCount} 张）` }}
        </a-button>
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
            </span>
            <a-tag v-if="!c.auto_ok" size="small" color="red" :bordered="false">{{ c.note }}</a-tag>
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
                <div class="skip-meta">
                  {{ s.id.slice(0, 8) }}…<template v-if="s.created_at">
                    · {{ timeLabel({ created_at: s.created_at }) }}</template
                  >
                </div>
              </div>
              <a-button size="mini" type="text" @click="locateSkipped(s)">定位</a-button>
            </li>
          </ul>
        </a-collapse-item>
      </a-collapse>
    </template>

    <!-- 大图预览弹窗 -->
    <a-modal
      v-model:visible="previewOpen"
      title="预览原图"
      width="90%"
      :modal-style="{ maxWidth: '420px' }"
      :footer="false"
      @cancel="closePreview"
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
    </a-modal>

    <!-- 内容重复对比弹窗：左右并排展示裁剪结果与库中重复素材，删除权交给用户 -->
    <a-modal
      v-model:visible="showDupModal"
      title="裁剪结果与库中素材内容重复"
      width="92%"
      :modal-style="{ maxWidth: '860px' }"
      :footer="false"
      :closable="!dupProcessing"
      :mask-closable="!dupProcessing"
      :esc-to-close="!dupProcessing"
      @cancel="handleDupModalClose"
    >
      <template v-if="currentDup">
        <div class="dup-step">
          第 {{ dupTotal - (dupQueue.length - 1) }} / {{ dupTotal }} 组 ·
          裁剪后与库中素材内容一致（内容哈希相同）
        </div>
        <div class="dup-compare">
          <div class="dup-side">
            <div class="dup-side-label">裁剪结果（本次操作后）</div>
            <div class="dup-img-wrap">
              <img :src="dupPreviewUrl(currentDup)" alt="裁剪结果" />
            </div>
            <div class="dup-side-meta">{{ currentDup.id.slice(0, 8) }}…</div>
          </div>
          <div class="dup-vs">内容<br />一致</div>
          <div class="dup-side">
            <div class="dup-side-label">库中已有素材</div>
            <div class="dup-img-wrap">
              <img :src="dupTargetUrl(currentDup)" alt="库中重复素材" />
            </div>
            <div class="dup-side-meta">
              {{ currentDup.dup_id.slice(0, 8) }}…<template v-if="currentDup.dup_created_at">
                · {{ dupTimeLabel(currentDup) }}</template
              >
            </div>
          </div>
        </div>
        <p class="dup-hint">
          两张图内容相同，请选择保留哪一张（删除为<strong>永久删除</strong>，不可恢复）：
        </p>
        <div class="dup-actions">
          <a-popconfirm
            :disabled="dupProcessing"
            :ok-loading="dupProcessing"
            @ok="handleDupKeepCrop"
          >
            <template #content>
              将<strong>永久删除</strong>库中重复素材（文件与记录不可恢复），确定继续？
            </template>
            <a-button type="primary" :loading="dupProcessing" :disabled="dupProcessing">
              保留裁剪结果，删除库中重复素材
            </a-button>
          </a-popconfirm>
          <a-button :disabled="dupProcessing" @click="handleDupSkip">保留原图，跳过裁剪</a-button>
        </div>
      </template>
    </a-modal>
  </a-card>
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

/* 内容重复对比弹窗 */
.dup-step {
  font-size: 12px;
  color: #999;
  margin-bottom: 10px;
}

.dup-compare {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dup-side {
  flex: 1;
  min-width: 0;
}

.dup-side-label {
  font-size: 12px;
  color: #666;
  margin-bottom: 6px;
}

.dup-img-wrap {
  height: 52vh;
  max-height: 480px;
  background: #111;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.dup-img-wrap img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}

.dup-side-meta {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
}

.dup-vs {
  flex-shrink: 0;
  width: 52px;
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: #f0a020;
  line-height: 1.5;
}

.dup-hint {
  margin: 12px 0 10px;
  font-size: 13px;
  color: #333;
}

.dup-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
