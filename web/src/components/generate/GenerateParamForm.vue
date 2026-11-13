<script setup lang="ts">
/** 出图参数面板：模型档位、参考图、描述、尺寸/步数/张数/seed、出图配方与生成按钮。
 *
 * 从 GenerateView.vue 抽出（视图只做编排：拉环境状态 → 交给面板 → 面板回抛提交参数）。
 * 面板自带全部表单状态（选中档位、描述、配方名…），只有三类东西从外面来：
 * 环境状态（决定档位表与就绪标记）、useImageGen 的提交/取消状态、以及「从素材反推描述」
 * 的实现（沿用视图里那份，避免两处各写一遍错误提示）。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { CreateRunPayload, GenerateStatus } from '@/api/generate'
import { useGenerationPresets, type GenerationPreset } from '@/composables/useGenerationPresets'
import { useGenerateModelSelection } from '@/composables/useGenerateModelSelection'
import { shortModelLabel } from '@/utils/format'
import ReferencePickerModal from '@/components/generate/ReferencePickerModal.vue'
import PosePickerModal from '@/components/generate/PosePickerModal.vue'
import QuickPoseBatchPanel from '@/components/generate/QuickPoseBatchPanel.vue'
import { fetchPosePrompt, type PoseItem } from '@/api/poseGallery'
import { getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  /** 出图环境状态（档位表 / 就绪标记都来自它；null = 还没拉到） */
  status: GenerateStatus | null
  /** 环境是否就绪（未就绪时禁用生成按钮） */
  ready: boolean
  /** 提交中（按钮 loading 与防重复提交） */
  submitting: boolean
  /** 是否有正在跑的批次（有则显示「取消」） */
  active: boolean
  /** 从素材详情「用这张图改款」跳进来时带的参考素材 ID（空串 = 没有） */
  initialRefIds: string
  /** 档位**目录**缓存（来自上一次成功探测）：环境还在探测时先按它渲染档位。
   *
   *  为什么需要：`/generate/status` 实测 4.4 秒，这期间若只给「兜底两档」，用户会看到
   *  档位「一个个冒出来」，且 `?model=` 请求的档位此刻还不在列表里 → 选中失败。
   *  只用它渲染目录，**就绪状态一律先按未就绪**（`ready` 等真探测结果）。
   */
  modelsMetaFallback?: GenerateStatus['models_meta'] | null
  /** 从素材详情跳进来时**显式指定**的档位 key（如 `qwen_2_1_uc_i2i`，空串 = 不指定）。
   *
   *  用户点「图生图（未审查）」这个链接就是明确的选择，所以允许直接切到未审查档位；
   *  默认值仍然不会自动选中未审查权重（那条规则管的是「别替用户做风险选择」）。
   */
  initialModelKey?: string
  /** 从素材详情「换个姿势」跳进来：挂载后自动打开姿势选择器（空串=false） */
  initialPoseLib?: boolean
  /** 从素材详情「换个姿势」跳进来时默认走云端换姿势（不预选任何姿态）。
   *
   *  云端档位由 `seedream_5_0_flash` 提供；本地线稿换姿势仍保留为手动切换。
   */
  initialPoseCloud?: boolean
  /** 从素材反推描述（沿用视图里 useImageGen 的实现） */
  promptFrom: (ids: string[]) => Promise<string | null>
  /** 已选参考图的缩略图数据（视图按 referenceIds 拉好后传下来；顺序=参考顺序）。
   *
   *  为什么由视图拉：这个面板只该管表单状态，素材元数据（缩略图/原图路径）在视图层
   *  已经有一份拉取能力（对比弹窗也用它），两处都自己拉会重复请求同一批素材。
   */
  referenceItems?: Array<{ id: string; file_path: string; thumbnail_path?: string | null }>
}>()

/** 历史参数「带入表单」的载荷（来自生成历史的一行） */
export interface RunPayload {
  prompt?: string | null
  negative?: string | null
  model_key?: string | null
  reference_ids?: string[] | null
  params?: Record<string, unknown> | null
  /** 历史批次用过的姿势（视图按 params.pose_id 查好后传入；缺省/null = 清除当前姿势） */
  pose?: PoseItem | null
}

const emit = defineEmits<{
  /** 提交生成：面板把参数拼好后交给视图，由 useImageGen 提交 */
  (e: 'submit', payload: CreateRunPayload): void
  /** 云端多姿态：一次提交 N 个独立批次（每个姿态一个 run） */
  (e: 'submitBatch', payloads: CreateRunPayload[]): void
  (e: 'cancel'): void
  /** 参考图 id 变化（视图据此刷新缩略图；空数组=已清空） */
  (e: 'reference-ids-change', ids: string[]): void
  /** 点了某张已选参考的缩略图：视图打开大图浏览（参数=该张在参考顺序里的下标） */
  (e: 'browse-references', index: number): void
}>()

const {
  allModels,
  probing,
  modelKey,
  pendingModelKey,
  pendingKind,
  currentModel,
  modelGroups,
  modelTaskTitle,
  modelTaskDesc,
  isArk,
  supportsSeed,
  supportsNegative,
  requiresUpload,
  kind,
  modelReady,
  i2iReady,
  selectModelByKind,
  applyPoseMode,
  poseMode,
  POSE_CLOUD_MODEL_KEY,
} = useGenerateModelSelection(props)

// ── 参数 ──
const prompt = ref('')
const negative = ref('')
const count = ref(2)
const steps = ref(8)
const seed = ref(0)
/** 尺寸预设：不给自由输入，避免用户填出必 OOM 的值（见 docs 第 8 节） */
const sizePresets = [
  { label: '1024 × 1024（方形，最快）', value: '1024x1024' },
  { label: '1328 × 1328（更大，接近显存上限）', value: '1328x1328' },
  { label: '768 × 1344（竖版，适合穿搭）', value: '768x1344' },
]
const size = ref('1024x1024')
/** 当前预设对应的宽高。

原来是 `const [width, height] = computed(...).value` —— 那会在 setup 时求值一次并
把当时的 1024/1024 固定下来，用户换预设（1328²/竖版）根本不生效。必须保持响应式。
*/
const sizeDims = computed(() => size.value.split('x').map(Number))
const width = computed(() => sizeDims.value[0] ?? 1024)
const height = computed(() => sizeDims.value[1] ?? 1024)

const CLOUD_SIZE_PRESETS = [
  { label: '2K 竖版 1536 × 2048（推荐）', value: '1536x2048' },
  { label: '1.5K 竖版 1152 × 1536', value: '1152x1536' },
  { label: '1K 方形 1024 × 1024', value: '1024x1024' },
]
const cloudSize = ref('1536x2048')
const cloudWatermark = ref(false)
const cloudPoses = ref<PoseItem[]>([])
/** 云端可选姿态上限：后端 models_meta.max_poses（默认 10，可由 SEEDREAM_MAX_POSES 配置） */
const maxCloudPoses = computed(() => currentModel.value?.maxPoses ?? 10)
const cloudCost = computed(() =>
  isArk.value
    ? Math.max(1, cloudPoses.value.length) * (currentModel.value?.costPerImage || 0.12)
    : 0,
)

function onCloudPosesSelected(poses: PoseItem[]): void {
  // P1-5：云端一期是单人物模板 + 1 张素材图，双人姿态会被合成/截断，选的时候就拦掉
  const single = poses.filter((pose) => !(pose.system || '').includes('双人'))
  const dualCount = poses.length - single.length
  if (dualCount > 0) {
    Message.warning(`云端一期不支持双人姿态，已跳过 ${dualCount} 个（可改用本地出图）`)
  }
  if (single.length > maxCloudPoses.value) {
    Message.warning(`云端一次最多 ${maxCloudPoses.value} 个姿态，超出部分已忽略`)
  }
  cloudPoses.value = single.slice(0, maxCloudPoses.value)
  selectedPose.value = cloudPoses.value[0] ?? null
}

function removeCloudPose(id: string): void {
  cloudPoses.value = cloudPoses.value.filter((p) => p.id !== id)
  if (selectedPose.value?.id === id) selectedPose.value = cloudPoses.value[0] ?? null
}

// 云端档位固定 i2i + 1 张；切到云端时强制线稿模式并重置张数
watch(isArk, (ark) => {
  if (!ark) return
  poseMode.value = 'lineart'
  count.value = 1
})

// ── 参考素材 id（逗号/空格分隔，1~10 个）——i2i 用 ──
const refIdsText = ref('')
const refIdsList = computed(() => splitIds(refIdsText.value))

// ── 姿势参考（换姿势）：单选，姿势图占一张参考额度 ──
// 为什么占额度：执行器把姿势图作为**最后一张参考图**上传，Qwen-Image 2.1 最多 10 张，
// 所以选了姿势后主体参考最多 9 张（后端同样会校验，前端提前拦住给出可读提示）。
const posePickerVisible = ref(false)
const selectedPose = ref<PoseItem | null>(null)

// 用户点生成方式单选时同步档位（选中姿势后才需要切）
watch(poseMode, (mode) => {
  if (selectedPose.value) applyPoseMode(mode)
})
const maxRefs = computed(() => {
  if (isArk.value) return currentModel.value?.maxRefs ?? 1
  return selectedPose.value && poseMode.value === 'lineart' ? 9 : 10
})
const refCountOk = computed(
  () => refIdsList.value.length >= 1 && refIdsList.value.length <= maxRefs.value,
)

/** 姿势提示词缓存：同一姿势只查一次 `pose_prompts` 表。 */
const posePromptCache = new Map<string, string>()
/** 最近一次自动填入的提示词（用于判断"这是系统填的、可以整段替换"）。 */
const lastAutoPrompt = ref('')

/** 读取姿势已反推的提示词（后端 `pose_prompts` 表；没有则空串）。 */
async function loadPoseDbPrompt(pose: PoseItem): Promise<string> {
  const hit = posePromptCache.get(pose.id)
  if (hit) return hit
  try {
    const res = await fetchPosePrompt(pose.id)
    const text = (res.prompt ?? '').trim()
    if (text) posePromptCache.set(pose.id, text)
    return text
  } catch {
    // 查询失败不阻塞选姿势：生成时后端仍会补数据库提示词
    return ''
  }
}

/** 线稿参考模式：把 `pose_prompts` 的反推提示词填进描述框（保留用户手写内容）。 */
async function applyPoseDbPrompt(pose: PoseItem): Promise<boolean> {
  if (poseMode.value !== 'lineart') return false
  const text = await loadPoseDbPrompt(pose)
  if (!text) return false
  const current = prompt.value.trim()
  const legacy = current.includes('保持第 1 张参考图') || current.includes('人物姿势：')
  if (!current || current === lastAutoPrompt.value || legacy) {
    prompt.value = text
    lastAutoPrompt.value = text
  } else if (!current.includes(text)) {
    prompt.value = `${text}
${current}`
  }
  return true
}

/** 选中姿势：按当前生成方式切档位（默认文字描述/文生图，可切线稿参考/图生图） */
async function onPoseSelected(pose: PoseItem): Promise<void> {
  selectedPose.value = pose
  // 线稿参考固定用未审查第三档；文字描述用文生图第一档
  pendingModelKey.value = ''
  pendingKind.value = null
  applyPoseMode(poseMode.value)
  if (poseMode.value === 'lineart') await applyPoseDbPrompt(pose)
  Message.success(
    poseMode.value === 'text'
      ? `已选姿势：${pose.name}（文字描述生成）`
      : `已选姿势：${pose.name}（线稿参考生成）`,
  )
}

function clearPose(): void {
  selectedPose.value = null
}

/** 姿势体系的构图词：上半身不要硬写「全身照」，双人不要写成单人构图。 */
function poseFraming(system: string): string {
  if (system.includes('上半身')) return '上半身构图（半身照）'
  if (system.includes('双人')) return '双人合照'
  return '全身照'
}

/** 文生图的姿势描述：不引用参考图，直接写人物姿势（默认方式）。 */
function buildPoseTextPrompt(pose: PoseItem): string {
  return `人物姿势：${pose.name}（${pose.angle}），${poseFraming(pose.system)}。`
}

/** 插入一段「换姿势」描述模板（可再改）：优先用数据库反推提示词，缺记录时回退旧模板 */
async function insertPosePrompt(): Promise<void> {
  const pose = selectedPose.value
  if (!pose) return
  if (poseMode.value === 'lineart' && (await applyPoseDbPrompt(pose))) return
  const template = poseMode.value === 'lineart' ? buildPosePrompt(pose) : buildPoseTextPrompt(pose)
  // 连点两下不重复追加同一段
  if (prompt.value.includes(template)) return
  prompt.value = prompt.value.trim()
    ? `${prompt.value.trim()}
${template}`
    : template
}

/** 一键出图档位就绪：按当前生成方式判断——文字描述看 t2i，线稿参考看 i2i。 */
const quickModelReady = computed(() =>
  allModels.value.some((m) => m.kind === (poseMode.value === 'text' ? 't2i' : 'i2i') && m.ready),
)

/** 一键出图的用户基础描述（批次开始时锁定；每轮在它后面拼姿势模板，批次结束清空） */
const quickBasePrompt = ref('')

/** 生成一段「换姿势」描述（手动插入与一键出图共用；含姿势名/角度，历史里可区分批次） */
function buildPosePrompt(pose: PoseItem): string {
  return `保持第 1 张参考图的人物长相、发型与服装不变，按最后一张参考图的姿势（${pose.name}·${pose.angle}）重新生成，${poseFraming(pose.system)}。`
}

/** 一键出图的每一轮：套用面板给的姿势/张数/步数 → 切未审查档位 → 复用正常提交流程 */
async function onQuickRun(payload: {
  pose: PoseItem
  count: number
  steps: number
  round: number
}): Promise<void> {
  // 云端 Seedream 不走「性感系 399 姿势轮播」：它需要用户显式多选姿势，
  // 且每次只出 1 张（无 seed/多张概念）。误触时直接忽略，避免队列空转报失败。
  if (isArk.value) return
  count.value = payload.count
  steps.value = payload.steps
  selectedPose.value = payload.pose
  applyPoseMode(poseMode.value)

  // 描述：每轮都带**姿势名/角度**（否则一键出图几百批的历史描述完全一样，
  // 事后没法从历史/导出里看出这批用的是哪个姿势）。
  if (payload.round === 1) {
    const current = prompt.value.trim()
    // 上一轮一键出图留下的模板/数据库提示词不算「用户描述」：整段替换，避免反复叠加
    quickBasePrompt.value =
      current === lastAutoPrompt.value ||
      current.includes('按最后一张参考图的姿势') ||
      current.includes('人物姿势：')
        ? ''
        : current
  }
  let posePrompt: string
  if (poseMode.value === 'lineart') {
    const dbPrompt = await loadPoseDbPrompt(payload.pose)
    posePrompt = dbPrompt || buildPosePrompt(payload.pose)
    lastAutoPrompt.value = posePrompt
  } else {
    posePrompt = buildPoseTextPrompt(payload.pose)
    lastAutoPrompt.value = ''
  }
  prompt.value = quickBasePrompt.value
    ? `${quickBasePrompt.value}
${posePrompt}`
    : posePrompt

  // 防御：面板已禁用的场景不该真的提交，否则会出一批错图
  if (poseMode.value === 'text') {
    if (kind.value !== 't2i' || !modelReady.value) {
      Message.warning('一键出图未启动：请确认文生图档位就绪')
      return
    }
  } else if (kind.value !== 'i2i' || !refCountOk.value || !modelReady.value) {
    Message.warning('一键出图未启动：请确认已选参考图且图生图档位就绪')
    return
  }
  onSubmit()
}

/** 连续出图结束：释放批次锁定的基础描述（下一批重新采集） */
function onQuickBatchEnd(): void {
  quickBasePrompt.value = ''
}

/** 把「逗号/中文逗号/空白」分隔的 id 串拆成数组（两个输入框共用同一口径） */
function splitIds(text: string): string[] {
  return text
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

// ── 参考图图形化选择（替代粘 ID）──
const pickerVisible = ref(false)
function onPickConfirm(ids: string[]): void {
  refIdsText.value = ids.join(', ')
}

// 参考 id 一变就抛给视图（缩略图条由视图拉数据后回传 referenceItems）。
// 用 join 后的字符串做 watch 键：computed 每次都是新数组，直接 watch 会反复触发。
watch(
  () => refIdsList.value.join(','),
  () => emit('reference-ids-change', refIdsList.value),
  { immediate: true },
)

/** 已选参考的缩略图地址（优先缩略图，省流量；没有则退回原图） */
function refThumb(item: { file_path: string; thumbnail_path?: string | null }): string {
  return getFileUrl(item.thumbnail_path || item.file_path)
}

// ── 出图配方（localStorage）──
const { presets, save: savePreset, remove: removePreset } = useGenerationPresets()
const presetName = ref('')
function currentPreset(): GenerationPreset {
  return {
    name: presetName.value,
    prompt: prompt.value,
    negative: negative.value,
    size: size.value,
    steps: steps.value,
    count: count.value,
    kind: kind.value,
  }
}
function onSavePreset(): void {
  if (savePreset(currentPreset())) {
    Message.success(`已保存配方「${presetName.value.trim()}」`)
    presetName.value = ''
  } else {
    Message.warning('请给配方起个名字')
  }
}
function onApplyPreset(name: string): void {
  const preset = presets.value.find((p) => p.name === name)
  if (!preset) return
  prompt.value = preset.prompt
  negative.value = preset.negative
  size.value = preset.size
  steps.value = preset.steps
  count.value = preset.count
  // 配方存的是生成方式，载入时换成对应档位（改款档位都没就绪就退回文生图档位）
  selectModelByKind(preset.kind === 'i2i' && i2iReady.value ? 'i2i' : 't2i')
  Message.success(`已载入配方「${preset.name}」`)
}

// ── 从素材反推 prompt（与参考图是不同的输入框：反推只是"读素材写描述"，不会拿它出图）──
const rewriteIdsText = ref('')
const previewing = ref(false)
async function onPreviewPrompt() {
  const ids = splitIds(rewriteIdsText.value)
  if (!ids.length) return
  previewing.value = true
  try {
    const text = await props.promptFrom(ids)
    if (text) prompt.value = text
  } finally {
    previewing.value = false
  }
}

// ── 生成 ──
const canSubmit = computed(
  () =>
    (!isArk.value ? !!prompt.value.trim() : true) &&
    !props.submitting &&
    !props.active &&
    modelReady.value &&
    (isArk.value
      ? cloudPoses.value.length >= 1 && refIdsList.value.length === 1
      : kind.value === 't2i' || refCountOk.value),
)

// ── 云端隐私确认（每个浏览器会话一次；不做永久同意）──
const privacyVisible = ref(false)
const privacyAgree = ref(false)
const pendingPayloads = ref<CreateRunPayload[]>([])
const ARK_PRIVACY_KEY = 'fashion-inspo:ark-upload-confirmed'

function needsPrivacyConfirm(): boolean {
  // 用档位能力声明判断，而不是硬编码 isArk：以后新增需要上传的档位自动走隐私确认
  if (!requiresUpload.value) return false
  try {
    return sessionStorage.getItem(ARK_PRIVACY_KEY) !== '1'
  } catch {
    return true
  }
}

function confirmPrivacy(): void {
  if (!privacyAgree.value) return
  try {
    sessionStorage.setItem(ARK_PRIVACY_KEY, '1')
  } catch {
    /* 隐私模式忽略 */
  }
  privacyVisible.value = false
  const payloads = pendingPayloads.value
  pendingPayloads.value = []
  if (payloads.length === 1) emit('submit', payloads[0])
  else if (payloads.length > 1) emit('submitBatch', payloads)
}

const activePreset = computed(() => sizePresets.find((p) => p.value === size.value))

function onSubmit() {
  if (!isArk.value && !prompt.value.trim()) return
  if (isArk.value) {
    if (cloudPoses.value.length === 0) {
      Message.warning('云端出图需要先从姿势图库选择至少 1 个姿态')
      return
    }
    if (refIdsList.value.length !== 1) {
      Message.warning('云端出图一期需要恰好 1 张素材图作参考')
      return
    }
    // P1-5：双人姿态在单人物模板 + 1 张素材图下必然失败；历史带入的也要拦住
    const dualPoses = cloudPoses.value.filter((pose) => (pose.system || '').includes('双人'))
    if (dualPoses.length) {
      Message.error(
        `云端一期不支持双人姿态：${dualPoses.map((pose) => pose.name).join('、')}，请移除后重试`,
      )
      return
    }
    const payloads: CreateRunPayload[] = cloudPoses.value.map((pose) => ({
      kind: 'i2i',
      prompt: prompt.value.trim() || '按图2姿势重新拍摄，保持图1人物长相与服装不变',
      count: 1,
      steps: 1,
      seed: 0,
      model_key: modelKey.value,
      reference_ids: refIdsList.value,
      pose_id: pose.id,
      size: cloudSize.value,
      watermark: cloudWatermark.value,
    }))
    if (needsPrivacyConfirm()) {
      pendingPayloads.value = payloads
      privacyAgree.value = false
      privacyVisible.value = true
      return
    }
    if (payloads.length === 1) emit('submit', payloads[0])
    else emit('submitBatch', payloads)
    return
  }
  // 姿势两种方式各自要求对应档位：文字=文生图；线稿=图生图且至少 1 张主体参考
  if (selectedPose.value) {
    if (poseMode.value === 'text' && kind.value !== 't2i') {
      Message.warning('文字描述模式需要文生图档位：请切换档位，或把生成方式改成「线稿参考」')
      return
    }
    if (poseMode.value === 'lineart' && (kind.value !== 'i2i' || !refCountOk.value)) {
      Message.warning('线稿参考模式需要图生图档位，并先选 1 张以上主体参考图')
      return
    }
  }
  emit('submit', {
    kind: kind.value,
    prompt: prompt.value.trim(),
    negative: negative.value.trim() || undefined,
    width: width.value,
    height: height.value,
    steps: steps.value,
    count: count.value,
    seed: seed.value,
    // 只有拿到档位表且是图生图时才显式指定；否则交给后端按 kind 取默认
    model_key: kind.value === 'i2i' && modelKey.value ? modelKey.value : undefined,
    reference_ids: kind.value === 'i2i' ? refIdsList.value : undefined,
    // 姿势两种模式都带 id：文生图用于历史可追溯，图生图额外把线稿作为最后一张参考
    pose_id: selectedPose.value ? selectedPose.value.id : undefined,
  })
}

/**
 * 把历史批次的参数带进表单（**不直接生成**，用户可先改 seed / 参考图）。
 *
 * 只覆盖表单里确实存在的字段；档位只在当前档位表里有时才切换
 * （否则历史里的旧档位会把表单切到一个不存在的选项上）。
 */
function applyRunPayload(payload: RunPayload): void {
  if (typeof payload.prompt === 'string') prompt.value = payload.prompt
  if (typeof payload.negative === 'string') negative.value = payload.negative
  // 姿势：历史里没有姿势就清空（避免把上一个批次的姿势带进新批次）
  selectedPose.value = payload.pose ?? null

  const params = payload.params ?? {}
  const num = (key: string): number | null => {
    const value = params[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  }
  const w = num('width')
  const h = num('height')
  if (w && h) {
    const preset = `${w}x${h}`
    // 非预设尺寸（历史里手改过）时不硬塞：保留当前预设，避免出现空选项
    if (sizePresets.some((item) => item.value === preset)) size.value = preset
  }
  const n = num('steps')
  if (n) steps.value = n
  const c = num('count')
  if (c) count.value = c
  const s = num('seed_base')
  if (s !== null) seed.value = s

  const refIds = payload.reference_ids ?? []
  refIdsText.value = refIds.join(', ')
  if (refIds.length) {
    selectModelByKind('i2i')
    if (kind.value !== 'i2i') pendingKind.value = 'i2i'
  }
  if (payload.model_key && allModels.value.some((m) => m.key === payload.model_key)) {
    modelKey.value = payload.model_key
    pendingModelKey.value = ''
  }
  // P1-4：云端档位的 size / 水印 / 姿态不在本地参数（宽高/steps）里，
  // 带入表单时必须一并恢复，否则「带入历史参数」后重跑会悄悄变回默认值
  const isCloudRun = !!(
    payload.model_key &&
    allModels.value.some((m) => m.key === payload.model_key && m.provider === 'ark')
  )
  if (isCloudRun) {
    const cloudSizeRaw = typeof params.size === 'string' ? params.size : ''
    if (cloudSizeRaw && CLOUD_SIZE_PRESETS.some((item) => item.value === cloudSizeRaw)) {
      cloudSize.value = cloudSizeRaw
    }
    cloudWatermark.value = params.watermark === true
    cloudPoses.value = payload.pose ? [payload.pose] : []
    selectedPose.value = cloudPoses.value[0] ?? null
  }
  // 姿势历史：按历史档位还原生成方式（i2i=线稿参考；t2i/缺省=文字描述）
  if (payload.pose) {
    const historyI2i = !!(
      payload.model_key &&
      allModels.value.some((m) => m.key === payload.model_key && m.kind === 'i2i')
    )
    poseMode.value = historyI2i ? 'lineart' : 'text'
    if (!payload.model_key) applyPoseMode(poseMode.value)
  }
  Message.success('已带入历史参数，可继续修改后生成')
}

defineExpose({ applyRunPayload })

onMounted(() => {
  // 从素材详情「换个姿势」跳进来：自动打开姿势选择器（参考图由 initialRefIds 预填）
  if (props.initialPoseLib) posePickerVisible.value = true
  // 从素材详情跳进来：预填参考图并切到改款档位
  if (props.initialRefIds) {
    refIdsText.value = props.initialRefIds
    // 档位表已到（有目录缓存）→ 立刻切；还没到（首访）→ 记下意图，由 allModels 的 watch 认领。
    // 不记的话会落到默认的文生图档位，参考素材填了也不生效（t2i 不提交 reference_ids）
    selectModelByKind('i2i')
    if (kind.value !== 'i2i') pendingKind.value = 'i2i'
  }
  // 显式指定了档位（「图生图（未审查）」链接）→ 立刻切过去；档位表晚到的话由
  // allModels 的 watch 兜底（pendingModelKey 只认一次，避免覆盖用户后续手选）
  if (props.initialModelKey && allModels.value.some((m) => m.key === props.initialModelKey)) {
    modelKey.value = props.initialModelKey
    pendingModelKey.value = ''
  }
  // 换姿势流程最后定档位：默认「线稿参考」→ 未审查第三档（覆盖前面 initialRefIds
  // 选中的普通改款第二档）；用户仍可在面板切「文字描述」换成文生图第一档。
  // 2026-11-12：从详情「换个姿势」默认走云端换姿势，不预选姿态；云端档位未就绪时
  // 不强行切本地（保留用户选择/默认档位）。
  if (props.initialPoseCloud) {
    cloudPoses.value = []
    selectedPose.value = null
    pendingKind.value = null
    if (allModels.value.some((m) => m.key === POSE_CLOUD_MODEL_KEY)) {
      modelKey.value = POSE_CLOUD_MODEL_KEY
      pendingModelKey.value = ''
    } else {
      // 档位表还没回来：等 allModels 的 watch 认领云端档位
      pendingModelKey.value = POSE_CLOUD_MODEL_KEY
    }
  } else if (props.initialPoseLib) {
    pendingModelKey.value = ''
    pendingKind.value = null
    applyPoseMode(poseMode.value)
  }
})
</script>

<template>
  <div class="gen-form">
    <!-- ① 生成方式：按任务选，而不是按模型名选 -->
    <div class="gen-section-head">
      <span class="gen-section-title">选择生成方式</span>
      <span class="gen-hint">按「你要做什么」选，不用记模型名</span>
    </div>
    <div v-if="!allModels.length" class="gen-hint gen-model-loading">
      正在读取模型档位…（本机要探测 ComfyUI 与权重文件，约 4 秒）
    </div>
    <a-radio-group v-else v-model="modelKey" class="gen-models">
      <template v-for="group in modelGroups" :key="group.key">
        <div class="gen-model-group">{{ group.label }}</div>
        <a-radio v-for="m in group.models" :key="m.key" :value="m.key" class="gen-model-card">
          <span class="gen-model-card-top">
            <span class="gen-model-card-title">{{ modelTaskTitle(m) }}</span>
            <span class="gen-model-state" :class="{ 'is-ready': m.ready }">
              <span class="gen-model-dot" />
              {{ m.ready ? '就绪' : probing ? '探测中' : '未就绪' }}
            </span>
          </span>
          <span class="gen-model-card-name">{{ shortModelLabel(m.label) }}</span>
          <span class="gen-model-card-desc">{{ modelTaskDesc(m) }}</span>
          <span class="gen-model-card-meta">
            <a-tag :color="m.provider === 'ark' ? 'orange' : 'gray'" size="small">
              {{ m.provider === 'ark' ? '云端' : '本地' }}
            </a-tag>
            <a-tag v-if="m.uncensored" color="red" size="small">未审查</a-tag>
            <span class="gen-hint">{{ m.kind === 'i2i' ? '需参考图' : '纯文字' }}</span>
            <span v-if="m.costPerImage" class="gen-hint">¥{{ m.costPerImage.toFixed(2) }}/张</span>
          </span>
        </a-radio>
      </template>
    </a-radio-group>
    <div v-if="probing && allModels.length" class="gen-hint gen-model-loading">
      档位已列出（用上次的目录），就绪状态正在探测…
    </div>
    <a-alert v-if="currentModel?.uncensored" type="warning" class="gen-alert gen-alert--tight">
      {{ currentModel.notice || '该档位使用无内容安全过滤的权重。' }}
    </a-alert>

    <!-- ② 参考图（仅图生图）：先选图，再写改款描述 -->
    <template v-if="kind === 'i2i'">
      <div class="gen-section-head">
        <span class="gen-section-title">选择参考图</span>
        <span class="gen-hint">{{ refIdsList.length }}/{{ maxRefs }} · 第 1 张是主参考</span>
      </div>
      <div class="gen-row">
        <a-input
          v-model="refIdsText"
          :placeholder="isArk ? '参考素材 ID（1 个）' : '参考素材 ID（1~10 个，逗号分隔）'"
          allow-clear
        />
        <a-button type="outline" @click="pickerVisible = true">选择图片</a-button>
      </div>
      <div class="gen-hint">
        参考图决定人物、场景与版型；描述要写「改完之后的样子」（例：「同一个人、同一场景，光脚不穿鞋」），
        只写命令（如「去掉鞋子」）容易失效。出图比例跟随参考图。
      </div>
      <div v-if="(referenceItems?.length ?? 0) > 0" class="gen-ref-strip">
        <div class="gen-ref-strip-head">
          <span class="gen-label gen-label--inline">已选参考（{{ referenceItems?.length }}）</span>
          <span class="gen-hint">点任意一张看大图 · 第 1 张是主参考</span>
        </div>
        <div class="gen-ref-thumbs">
          <button
            v-for="(item, idx) in referenceItems"
            :key="item.id"
            type="button"
            class="gen-ref-thumb"
            :title="`第 ${idx + 1} 张 · ${item.id}`"
            @click="emit('browse-references', idx)"
          >
            <img :src="refThumb(item)" alt="" loading="lazy" />
            <span class="gen-ref-thumb-index">{{ idx + 1 }}</span>
          </button>
        </div>
      </div>
    </template>

    <!-- ③ 描述：放在输入区靠上的位置，不再被姿势/批量面板挤到下面 -->
    <div class="gen-section-head">
      <span class="gen-section-title">描述画面</span>
      <span class="gen-hint">
        {{ isArk ? '可留空，系统会按姿态补默认描述' : '主体 + 穿搭 + 场景 + 光线' }}
      </span>
    </div>
    <a-textarea
      v-model="prompt"
      :placeholder="
        isArk
          ? '可留空；也可写「保持图1人物长相与服装，按图2姿势重新拍摄」'
          : '例：米色针织开衫配白色阔腿裤，极简工作室背景，柔和自然光，时尚编辑摄影'
      "
      :auto-size="{ minRows: 4, maxRows: 8 }"
      allow-clear
    />
    <div class="gen-row gen-row--inline">
      <a-input
        v-model="rewriteIdsText"
        placeholder="从素材反推：粘贴素材 ID（逗号分隔）"
        allow-clear
      />
      <a-button :loading="previewing" @click="onPreviewPrompt">反推</a-button>
    </div>
    <div class="gen-hint">反推会读取这些素材的标签、正文与主色拼成描述（可再手动改）。</div>

    <!-- ④ 姿势：云端多选；本地单选 + 可选的批量轮播 -->
    <div v-if="isArk" class="gen-pose">
      <div class="gen-section-head">
        <span class="gen-section-title">选择姿态</span>
        <span class="gen-hint">可多选，每个姿态独立生成、独立计费</span>
      </div>
      <div class="gen-row">
        <a-button @click="posePickerVisible = true">
          选择多个姿势（≤{{ maxCloudPoses }}）
        </a-button>
        <span class="gen-hint">已选 {{ cloudPoses.length }} / {{ maxCloudPoses }} 个姿态</span>
      </div>
      <div v-if="cloudPoses.length" class="gen-pose-cards">
        <div v-for="pose in cloudPoses" :key="pose.id" class="gen-pose-card">
          <img :src="pose.thumb_url" :alt="pose.name" loading="lazy" />
          <div class="gen-pose-meta">
            <div class="gen-pose-name">{{ pose.name }} · {{ pose.angle }}</div>
            <div v-if="pose.usage_count !== undefined" class="gen-hint">
              此前已用 {{ pose.usage_count }} 次
            </div>
            <a-button size="mini" type="text" @click="removeCloudPose(pose.id)">移除</a-button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="gen-pose">
      <div class="gen-section-head">
        <span class="gen-section-title">姿势（可选）</span>
        <span class="gen-hint">从姿势图库挑一个姿势</span>
      </div>
      <div class="gen-row">
        <a-button @click="posePickerVisible = true">
          {{ selectedPose ? '更换姿势' : '选择姿势（换姿势）' }}
        </a-button>
        <a-button v-if="selectedPose" type="text" @click="insertPosePrompt">插入姿势描述</a-button>
        <a-button v-if="selectedPose" type="text" @click="clearPose">清除</a-button>
        <span v-if="!selectedPose" class="gen-hint">
          默认按文字描述生成（文生图）；也可切线稿参考（图生图）
        </span>
      </div>
      <div v-if="selectedPose" class="gen-row">
        <span class="gen-label gen-label--inline">姿势生成方式</span>
        <a-radio-group v-model="poseMode" size="small">
          <a-radio value="text">文字描述（文生图）</a-radio>
          <a-radio value="lineart">线稿参考（图生图）</a-radio>
        </a-radio-group>
      </div>
      <div v-if="selectedPose" class="gen-pose-card">
        <img :src="selectedPose.thumb_url" :alt="selectedPose.name" loading="lazy" />
        <div class="gen-pose-meta">
          <div class="gen-pose-name">{{ selectedPose.name }} · {{ selectedPose.angle }}</div>
          <div class="gen-hint">
            {{ selectedPose.system }} / {{ selectedPose.category }} ·
            <template v-if="poseMode === 'lineart'">
              姿势图会作为<strong>最后一张</strong>参考图参与生成（占 1 张额度，需先选主体参考）
            </template>
            <template v-else>
              姿势名/角度会写进描述，用<strong>文生图</strong>生成（不需要参考图）
            </template>
            <template v-if="selectedPose.usage_count !== undefined">
              · 此前已用 {{ selectedPose.usage_count }} 次
            </template>
          </div>
        </div>
      </div>

      <!-- 批量轮播只服务「线稿参考（图生图）」；文生图下隐藏，避免误以为能直接批量 -->
      <QuickPoseBatchPanel
        v-if="kind === 'i2i'"
        :platform-ready="ready"
        :model-ready="quickModelReady"
        :pose-mode="poseMode"
        :has-references="refIdsList.length > 0"
        :busy="submitting || active"
        @run="onQuickRun"
        @batch-end="onQuickBatchEnd"
      />
    </div>

    <!-- ⑤ 高级参数：默认收起，减少首屏噪音；尺寸/张数在提交栏有摘要 -->
    <a-collapse class="gen-advanced" :bordered="false">
      <a-collapse-item
        key="advanced"
        :header="isArk ? '高级参数（尺寸 / 水印）' : '高级参数（尺寸 / 步数 / seed / 负向）'"
      >
        <template v-if="!isArk">
          <div class="gen-row gen-row--multi">
            <a-select v-model="size" :options="sizePresets" style="flex: 1" />
            <a-input-number v-model="count" :min="1" :max="4" style="width: 100px">
              <template #prefix>张数</template>
            </a-input-number>
          </div>
          <template v-if="supportsNegative">
            <div class="gen-label">负向描述（可选）</div>
            <a-input v-model="negative" placeholder="例：模糊、变形、多余手指" allow-clear />
          </template>
          <div class="gen-row">
            <a-input-number v-model="steps" :min="1" :max="30" style="width: 110px">
              <template #prefix>步数</template>
            </a-input-number>
            <a-input-number v-if="supportsSeed" v-model="seed" :min="0" style="width: 180px">
              <template #prefix>seed</template>
            </a-input-number>
          </div>
          <div v-if="supportsSeed" class="gen-hint">
            seed=0 时随机；提交后会把真实 seed 显示在结果区，便于复现。
          </div>
        </template>
        <template v-else>
          <div class="gen-row gen-row--multi">
            <a-select v-model="cloudSize" :options="CLOUD_SIZE_PRESETS" style="flex: 1" />
            <a-switch v-model="cloudWatermark" />
            <span class="gen-hint">加水印（默认关）</span>
          </div>
          <div class="gen-hint">
            云端单次固定 1 张，无 seed / 负向词 / 步数；素材图 + 姿态图会上传到火山引擎方舟。
          </div>
        </template>
      </a-collapse-item>
    </a-collapse>

    <!-- ⑥ 配方：次要功能，放在提交前，保持可发现但不抢主流程 -->
    <div class="gen-section-head">
      <span class="gen-section-title">出图配方</span>
      <span class="gen-hint">保存当前参数，下次一键载入</span>
    </div>
    <div class="gen-row">
      <a-input
        v-model="presetName"
        placeholder="配方名，如「极简工作室」"
        allow-clear
        style="flex: 1"
      />
      <a-button :disabled="!presetName.trim()" @click="onSavePreset">保存配方</a-button>
    </div>
    <div v-if="presets.length" class="gen-preset-list">
      <a-tag
        v-for="p in presets"
        :key="p.name"
        closable
        class="gen-preset-tag"
        @click="onApplyPreset(p.name)"
        @close="removePreset(p.name)"
      >
        {{ p.name }}
      </a-tag>
    </div>

    <!-- ⑦ 提交栏：sticky 在面板底部，长表单滚动时不用再翻到底 -->
    <div class="gen-actions">
      <div class="gen-actions-info">
        <span class="gen-actions-mode">
          {{ currentModel ? modelTaskTitle(currentModel) : '—' }}
        </span>
        <span v-if="isArk" class="gen-hint">
          云端 · 本次云端预计费用 ¥{{ cloudCost.toFixed(2) }}（{{ Math.max(1, cloudPoses.length) }}
          个姿态）
        </span>
        <span v-else class="gen-hint">
          本地 · {{ activePreset?.label || size }} · {{ count }} 张
        </span>
      </div>
      <div class="gen-actions-buttons">
        <a-button
          type="primary"
          :loading="submitting"
          :disabled="!canSubmit || !ready"
          @click="onSubmit"
        >
          生成 {{ isArk ? Math.max(1, cloudPoses.length) : count }} 张
        </a-button>
        <a-button v-if="active" status="warning" @click="emit('cancel')">取消</a-button>
      </div>
    </div>
  </div>

  <ReferencePickerModal
    v-model:visible="pickerVisible"
    :selected-ids="refIdsList"
    :max="maxRefs"
    @confirm="onPickConfirm"
  />

  <PosePickerModal
    v-model:visible="posePickerVisible"
    :selected-id="selectedPose?.id"
    :multiple="isArk"
    :selected-ids="cloudPoses.map((p) => p.id)"
    @select="onPoseSelected"
    @select-multiple="onCloudPosesSelected"
  />

  <a-modal
    v-model:visible="privacyVisible"
    title="云端出图需要上传图片"
    :ok-button-props="{ disabled: !privacyAgree }"
    ok-text="继续生成"
    cancel-text="取消"
    @ok="confirmPrivacy"
  >
    <p>
      本次生成会把「素材图」和「姿态图」上传到火山引擎方舟（cn-beijing）用于推理。
      素材涉及人物/人脸时，请确认你有权上传并接受云端处理。
    </p>
    <p>云端接口可能触发内容审核；被拦截时会给出提示，不会自动绕过。</p>
    <a-checkbox v-model="privacyAgree">我已知晓，本次允许上传</a-checkbox>
  </a-modal>
</template>

<style scoped src="./GenerateParamForm.css"></style>
