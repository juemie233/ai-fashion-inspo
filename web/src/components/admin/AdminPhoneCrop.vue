<script setup lang="ts">
/** 手机图剪裁面板：扫描候选（只读预览）→ 手动勾选确认 → 执行裁剪 → 自动入队向量回填。 */

import { reactive, ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import axios from 'axios'
import apiClient from '@/api/client'
import { getFileUrl, deleteInspiration } from '@/api/inspirations'
import { normalizeApplyResult, type CropApplyResult, type CropDuplicate } from '@/utils/cropResult'
import DensityImageGrid from '@/components/common/DensityImageGrid.vue'

const router = useRouter()

/** 从请求异常中提取后端 detail 文案（非 Axios 错误返回空串） */
function errorDetail(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: string } | undefined)?.detail
    return typeof detail === 'string' ? detail : ''
  }
  return ''
}

/** 裁剪模式：auto 黑边自动检测（小红书截图）/ ratio 固定比例 / content 内容边界检测（抖音截图） */
const mode = ref<'auto' | 'ratio' | 'content'>('auto')
/** AI 复核（较慢）：VLM 逐候选判断顶部状态栏/底部进度条，阳性置顶+标注，勾选权仍在用户 */
const vlmReview = ref(true)
/** 顶部/底部裁剪比例（百分比，仅 ratio 模式生效） */
const cropTop = ref(3)
const cropBottom = ref(5)
/** 单次最多返回候选数 */
const limit = ref(200)

/** a-form 表单模型（Arco 表单要求绑定 model；本面板无校验规则，仅提供上下文） */
const formModel = reactive({ mode, cropTop, cropBottom, limit, vlmReview })

const scanning = ref(false)
const cropping = ref(false)
/** 分页游标：后端按时间预算分批扫描时记录断点，供「继续扫描」续扫 */
const nextCursor = ref<string | null>(null)
/** 本次会话累计扫描的素材数（多次续扫累加） */
const scannedCount = ref(0)
/** 最近一次扫描的 AI 复核命中数（阳性候选已置顶展示） */
const vlmHits = ref(0)

// 扫描参数变化时已有断点作废（游标与参数无关，继续扫描会混用新旧参数），
// 重置断点与累计计数，下次扫描从头开始
watch([mode, cropTop, cropBottom, limit, vlmReview], () => {
  nextCursor.value = null
  scannedCount.value = 0
  vlmHits.value = 0
})

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
  /** content 模式：gray_band（灰带包夹）/ status_bar（状态栏+播放器条）/ plain / glyph_only（字形证据，行剖面无信号） */
  boundary_kind?: 'gray_band' | 'status_bar' | 'plain' | 'glyph_only' | null
  /** 后端勾选决策：字形证据（左右两角齐备）的残留候选默认勾选，无字形证据的不勾。
   * 旧响应无此字段时回退到「带建议比例即勾选」的兼容推断 */
  auto_checked?: boolean | null
  /** AI 复核阳性：VLM 判断顶部状态栏/底部进度条存在（勾选权仍在用户，仅置顶+标注） */
  vlm_residue?: boolean
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

/** 候选网格密度（紧凑/标准/宽松），默认标准；偏好持久化，与素材库页面行为一致 */
type CropGridDensity = 'compact' | 'standard' | 'comfortable'
const gridDensity = ref<CropGridDensity>(
  (localStorage.getItem('phone-crop-grid-density') as CropGridDensity) || 'standard',
)
watch(gridDensity, (v) => {
  localStorage.setItem('phone-crop-grid-density', v)
})

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
    // 已处理的素材不再出现在候选列表（从头重扫，避免沿用过期的分页断点）
    handleRescan()
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
  if (r && r.processed > 0) handleRescan()
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
/** 复制素材 ID 到剪贴板（反馈漏检/误勾问题时用） */
async function copyId(id: string) {
  try {
    await navigator.clipboard.writeText(id)
    Message.success('素材 ID 已复制')
  } catch {
    Message.error('复制失败，请手动选择复制')
  }
}

/** 新开浏览器标签页查看素材详情（不离开剪裁页，保留扫描/勾选状态） */
function openPreviewDetail() {
  const id = previewId.value
  if (!id) return
  previewOpen.value = false
  const { href } = router.resolve({ path: '/detail/' + id })
  window.open(href, '_blank', 'noopener,noreferrer')
}

/** 大图预览：URL + 关联素材 ID（展示于弹窗，供用户反馈问题时复制） */
const previewUrl = ref('')
const previewId = ref('')

/** 打开大图预览 */
function openPreview(url: string, id = '') {
  previewUrl.value = url
  previewId.value = id
  previewOpen.value = true
}

/** 关闭大图预览 */
function closePreview() {
  previewOpen.value = false
  previewUrl.value = ''
  previewId.value = ''
}

const scannedTotal = ref(0)
const checkedCount = computed(() => checkedIds.value.size)

/** 是否只勾选高置信候选（默认勾选 high+medium，排除 low；content 模式 plain
 * 类型无灰带/状态栏结构，可能是普通照片暗部，同样不默认勾选）。
 * content 模式后端已按自动化口径过滤：列表内仅剩手机截图候选（UI 特征或
 * 状态栏残留）。「疑似状态栏残留」候选（auto_ok=false 但带建议裁剪比例）
 * 也默认勾选——一次扫描即自动选上，免逐张手动确认；执行前有网格预览 +
 * 原图自动备份兜底。 */
function defaultCheckedIds(items: CropCandidate[]): Set<string> {
  const ids = new Set<string>()
  for (const c of items) {
    if (c.auto_ok) {
      // 后端勾选决策优先：auto 模式仅双侧黑边（小红书截图形态）勾选；
      // content 模式仅强字形证据勾选。旧响应无此字段时回退到历史规则
      if (typeof c.auto_checked === 'boolean') {
        if (c.auto_checked) ids.add(c.id)
        continue
      }
      if (c.confidence === 'low') continue
      if (c.boundary_kind === 'plain') continue
      ids.add(c.id)
      continue
    }
    // 疑似状态栏残留：后端已按字形证据给出勾选决策（左右两角齐备才自动勾选，
    // 纯色背景照片/海报大字等无字形候选不勾）——直接采用后端决策
    if (typeof c.auto_checked === 'boolean') {
      if (c.auto_checked) ids.add(c.id)
      continue
    }
    // 旧响应兼容：后端给出建议裁剪比例即默认勾选（crop_top=残留建议值）
    if (c.crop_top > 0) ids.add(c.id)
  }
  return ids
}

/** 扫描候选：只读预览，不修改任何数据。
 * 素材量大时后端按时间预算分批返回（truncated + next_cursor）：
 * 首次扫描重置列表；「继续扫描」把新批次追加合并到现有列表（按 id 去重，
 * 保留已勾选状态），避免续扫丢弃上一批候选；单次请求超时放宽到 120s。 */
interface ScanResponse {
  total: number
  items: CropCandidate[]
  scanned: number
  next_cursor: string | null
  truncated: boolean
  /** 本次扫描是否执行了 AI 复核（vlm_review 开启且有候选） */
  vlm_reviewed?: boolean
  /** AI 复核检出系统 UI 残留的候选数（已置顶展示） */
  vlm_hits?: number
}

async function handleScan() {
  scanning.value = true
  result.value = null
  const isContinuation = nextCursor.value !== null
  try {
    const { data } = await apiClient.post<ScanResponse>(
      '/admin/crop-phone-screenshots/scan',
      {
        mode: mode.value,
        crop_top: cropTop.value / 100,
        crop_bottom: cropBottom.value / 100,
        limit: limit.value,
        cursor: nextCursor.value ?? undefined,
        time_budget: 60,
        vlm_review: vlmReview.value,
      },
      { timeout: 120000 },
    )
    nextCursor.value = data.truncated ? data.next_cursor : null
    scannedCount.value += data.scanned
    vlmHits.value = data.vlm_hits ?? 0
    if (vlmHits.value > 0) {
      Message.info(`AI 复核检出 ${vlmHits.value} 张候选存在系统 UI 残留，已置顶展示`)
    }
    if (isContinuation) {
      // 续扫：追加合并新批次（后端按稳定顺序分批，新批次是尚未扫描的剩余素材）
      const known = new Set(candidates.value.map((c) => c.id))
      const fresh = data.items.filter((c) => !known.has(c.id))
      candidates.value = [...candidates.value, ...fresh]
      checkedIds.value = new Set([...checkedIds.value, ...defaultCheckedIds(fresh)])
      scannedTotal.value += data.total
    } else {
      candidates.value = data.items
      checkedIds.value = defaultCheckedIds(data.items)
      scannedTotal.value = data.total
    }
    if (data.items.length === 0 && !data.truncated) {
      Message.info(isContinuation ? '扫描完成，剩余素材中没有新的候选' : '没有可裁剪的竖屏截图素材')
    }
  } catch (e: unknown) {
    Message.error(errorDetail(e) || '扫描失败')
  } finally {
    scanning.value = false
  }
}

/** 重新扫描（重置断点，从头开始）；裁剪成功后的列表刷新也走这里 */
function handleRescan() {
  nextCursor.value = null
  scannedCount.value = 0
  return handleScan()
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
      // （从头重扫，避免沿用过期的分页断点导致列表只剩下一批）
      await handleRescan()
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
</script>

<template>
  <a-card title="手机图剪裁" size="small" style="margin-bottom: 24px">
    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      扫描手动上传素材中的手机全屏截图（仅「手动上传 + 竖屏」），
      <b>人工勾选确认后</b>执行裁剪。两种自动模式各对应一个平台的截图形态：
      <b>「自动检测黑边（小红书截图）」</b>——小红书浏览态截图，上下黑边包夹图片主体，
      检出双侧黑边才默认勾选（单侧「黑边」多为抖音截图的播放器条或照片暗部，保留候选但不勾选，
      建议改用内容边界检测处理）；<b>「内容边界检测（抖音截图）」</b>——抖音全屏截图， 顶部透明状态栏
      + 底部播放器条，按状态栏字形证据默认勾选，无 UI 证据的普通竖屏照片静默排除。
      「固定比例」按设定比例裁剪，不区分平台。原图自动备份到
      <code>storage/_crop_backup/</code>，裁剪成功后自动入队向量回填； 标签/收藏等信息不动。开启「AI
      复核」时，VLM 逐候选判断顶部状态栏/底部进度条， 检出的候选置顶并标注（不代替人工勾选）。AI
      复核命中候选优先， 其余候选按上传时间倒序排列。点击缩略图可查看大图（预览中可复制素材 ID）。
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
          <a-radio value="auto">自动检测黑边（小红书截图）</a-radio>
          <a-radio value="content">内容边界检测（抖音截图）</a-radio>
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

      <a-form-item label="AI 复核">
        <a-switch v-model="vlmReview" />
        <span style="margin-left: 8px; font-size: 12px; color: #999">
          VLM 逐候选判断顶部状态栏/底部进度条，阳性置顶并标注（较慢，约 1.3 秒/张）
        </span>
      </a-form-item>

      <a-form-item label=" ">
        <a-button type="primary" :loading="scanning" @click="handleScan">
          {{ scanning ? '扫描中...' : nextCursor ? '继续扫描剩余素材' : '扫描候选' }}
        </a-button>
        <a-button v-if="nextCursor && !scanning" size="small" @click="handleRescan">
          重新扫描
        </a-button>
        <span style="margin-left: 12px; font-size: 12px; color: #999">
          默认勾选高/中置信候选，低置信需人工复核
        </span>
        <span v-if="scannedCount > 0" style="margin-left: 12px; font-size: 12px; color: #999">
          已扫描 {{ scannedCount }} 张素材
        </span>
        <span v-if="nextCursor" style="margin-left: 12px; font-size: 12px; color: #f0a020">
          素材量较大，已返回一批候选；点击「继续扫描剩余素材」把未扫描的素材追加到列表
        </span>
      </a-form-item>
    </a-form>

    <!-- 候选网格：人工勾选确认 -->
    <template v-if="candidates.length > 0">
      <a-divider style="margin: 12px 0" />
      <!-- 候选网格：通用密度网格组件，紧凑/标准/宽松可调，默认标准 -->
      <DensityImageGrid v-model:density="gridDensity">
        <template #header-left>
          <a-checkbox
            :model-value="checkedCount > 0 && checkedCount === candidates.length"
            :indeterminate="checkedCount > 0 && checkedCount < candidates.length"
            @change="toggleAll"
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
            @click="handleApply"
          >
            {{ cropping ? '裁剪中...' : `确认裁剪（${checkedCount} 张）` }}
          </a-button>
        </template>

        <div
          v-for="c in candidates"
          :key="c.id"
          class="crop-item"
          :class="[{ checked: checkedIds.has(c.id), failed: !c.auto_ok }, 'density-' + gridDensity]"
          @click="toggleCheck(c.id)"
          @dblclick="openPreview(thumbUrl(c), c.id)"
        >
          <img
            :src="thumbUrl(c)"
            :alt="c.id"
            loading="lazy"
            @click.stop="openPreview(thumbUrl(c), c.id)"
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
        <div
          v-if="previewId"
          style="
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 0 0 8px;
            font-size: 12px;
            color: #ddd;
          "
        >
          <span style="font-family: monospace">{{ previewId }}</span>
          <a-button size="mini" type="outline" @click="copyId(previewId)">复制 ID</a-button>
          <a-button size="mini" type="text" @click="openPreviewDetail"> 查看素材详情 </a-button>
        </div>
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
            <div class="dup-side-meta">{{ currentDup.dup_id.slice(0, 8) }}…</div>
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
/* 网格布局（列数/间距/密度切换）由通用组件 DensityImageGrid 承担 */

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
