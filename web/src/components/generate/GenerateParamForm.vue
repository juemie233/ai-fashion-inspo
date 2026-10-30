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
import { shortModelLabel } from '@/utils/format'
import ReferencePickerModal from '@/components/generate/ReferencePickerModal.vue'
import PosePickerModal from '@/components/generate/PosePickerModal.vue'
import QuickPoseBatchPanel from '@/components/generate/QuickPoseBatchPanel.vue'
import type { PoseItem } from '@/api/poseGallery'
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
  (e: 'cancel'): void
  /** 参考图 id 变化（视图据此刷新缩略图；空数组=已清空） */
  (e: 'reference-ids-change', ids: string[]): void
  /** 点了某张已选参考的缩略图：视图打开大图浏览（参数=该张在参考顺序里的下标） */
  (e: 'browse-references', index: number): void
}>()

// ── 模型档位（一个档位就决定生成方式：文生图 / 参考图改款）──
/** 档位选项。不 export：只在本组件内使用（ts-prune 不追踪 .vue 引用，导出会被判死代码）。 */
interface ModelOption {
  key: string
  label: string
  kind: 't2i' | 'i2i'
  uncensored: boolean
  notice: string
  ready: boolean
}

/** 旧后端（不返回 models_meta）时的兜底两档：只保证页面不变空白，不含未审查档位 */
const LEGACY_MODELS: ModelOption[] = [
  {
    key: 'z_image_int8',
    label: 'Z-Image-Turbo（文生图）',
    kind: 't2i',
    uncensored: false,
    notice: '',
    ready: true,
  },
  {
    key: 'qwen_2_1_i2i',
    label: 'Qwen-Image 2.1（参考图改款）',
    kind: 'i2i',
    uncensored: false,
    notice: '',
    ready: true,
  },
]

/** 把 `models_meta` 目录映射成档位列表；`ready` 由 `models` 决定（没探测到就一律未就绪） */
function toOptions(
  meta: GenerateStatus['models_meta'] | undefined,
  models: Record<string, string> | undefined,
): ModelOption[] {
  return Object.entries(meta ?? {}).map<ModelOption>(([key, m]) => ({
    key,
    label: m.label,
    kind: m.kind === 'i2i' ? 'i2i' : 't2i',
    uncensored: m.uncensored === true,
    notice: m.notice || '',
    ready: models?.[key] === 'ok',
  }))
}

/** 全部可选档位：跟着后端 `MODEL_SPECS` 走，加一个权重只改后端、前端不用发版。
 *
 * 三态，顺序很重要（别把「还没探测到」当成「后端不支持」）：
 * 1. 已有探测结果 → 用真实目录 + 真实就绪状态；
 * 2. 探测还没回来 → 用**上次的目录缓存**渲染（就绪状态先按未就绪），没有缓存则返回空
 *    （渲染「正在读取模型档位…」，而不是先闪两档假档位）；
 * 3. 探测回来了但后端没给 `models_meta`（旧后端兜底）→ 用内置两档。
 */
const allModels = computed<ModelOption[]>(() => {
  const models = props.status?.models
  const list = toOptions(props.status?.models_meta, models)
  if (list.length) return list
  if (!props.status) return toOptions(props.modelsMetaFallback ?? undefined, undefined)
  return LEGACY_MODELS.map((m) => ({ ...m, ready: models?.[m.key] === 'ok' }))
})

/** 环境探测是否还在进行（模板据此显示「正在读取…」提示） */
const probing = computed(() => !props.status)

/** 当前选中的档位 */
const modelKey = ref('')

/** 外部显式请求的档位（只生效一次：之后用户手动换档位不该被再次覆盖） */
const pendingModelKey = ref((props.initialModelKey ?? '').trim())

/** 「想要图生图」的待认领标记：从素材详情带 ref 跳进来、但档位表还没到时先记下意图。
 *
 *  没有它会落到默认的文生图档位（watch 只挑「就绪且非未审查」的第一个），
 *  参考素材虽然填进输入框，提交时 `kind==='t2i'` 却不会带 reference_ids —— 静默失效。
 */
const pendingKind = ref<'i2i' | null>(null)

/** 默认档位：优先**就绪**且**非未审查**的。
 *
 * 未审查版必须由用户主动点选 —— 默认选中「无内容过滤」的权重，等于替用户做了一次
 * 他自己不知道的风险选择。已选中的档位仍然存在时不动它（刷新环境不重置用户选择）。
 *
 * 例外：``initialModelKey`` 来自用户在素材详情里**主动点**的那个链接（它写着「未审查」），
 * 属于明确的知情选择，因此直接切过去，并在拿到档位表后补一次提示。
 */
watch(
  allModels,
  (list) => {
    if (!list.length) return
    const wanted = pendingModelKey.value
    if (wanted) {
      const hit = list.find((m) => m.key === wanted)
      if (hit) {
        // 只有**真的找到**才算认领：环境还在探测时列表里没有这个档位，
        // 提前清空会让「从详情页带 ?model= 跳进来」永远选不到未审查版（真实缺陷）
        pendingModelKey.value = ''
        modelKey.value = hit.key
        Message.info(`已切到「${hit.label}」，参考素材 ID 已填好`)
        return
      }
    }
    if (pendingKind.value) {
      // 「用这张图改款」的意图优先于默认挑选：先按生成方式找档位（就绪且非未审查优先）
      const pool = list.filter((m) => m.kind === pendingKind.value)
      const pick =
        pool.find((m) => m.ready && !m.uncensored) ?? pool.find((m) => m.ready) ?? pool[0]
      if (pick) {
        pendingKind.value = null
        modelKey.value = pick.key
        return
      }
    }
    if (modelKey.value && list.some((m) => m.key === modelKey.value)) return
    const pickable = list.filter((m) => m.ready)
    const pool = pickable.length ? pickable : list
    modelKey.value = (pool.find((m) => !m.uncensored) ?? pool[0]).key
  },
  { immediate: true },
)

const currentModel = computed<ModelOption | null>(
  () => allModels.value.find((m) => m.key === modelKey.value) ?? null,
)

/** 生成方式**由档位推导**：Z-Image 是纯文生图，Qwen 档位要参考图，不再单独选一次 */
const kind = computed<'t2i' | 'i2i'>(() => currentModel.value?.kind ?? 't2i')

/** 选中的档位是否就绪：权重没放对目录时不能默认它能用 */
const modelReady = computed(() => currentModel.value?.ready ?? false)

/** 是否有可用的参考图改款档位（配方载入要用） */
const i2iReady = computed(() => allModels.value.some((m) => m.kind === 'i2i' && m.ready))

/** 按生成方式挑一个档位（配方载入 / 从素材详情跳转用）：优先就绪且非未审查 */
function selectModelByKind(target: 't2i' | 'i2i'): void {
  const pool = allModels.value.filter((m) => m.kind === target)
  const pick = pool.find((m) => m.ready && !m.uncensored) ?? pool.find((m) => m.ready) ?? pool[0]
  if (pick) modelKey.value = pick.key
}

/** 换姿势默认走**未审查版**改款档位（`qwen_2_1_uc_i2i`，档位表里的第三个）。
 *
 * 为什么不像「用这张图改款」那样按 kind 挑「就绪且非未审查」：姿势图库里包含成人向
 * 姿势，有审查权重会拦提示词/改图，换姿势经常直接失效；而用户点「换个姿势」本身
 * 就是明确的知情选择（详情页按钮、姿势选择器都只在这个流程出现），所以默认用未审查版。
 *
 * 兜底：旧后端没有未审查档位（或权重没放好）时退回就绪的普通改款档位，保证还能用；
 * 档位表还没探测回来时记下意图（pendingModelKey），等列表到达由 watch 认领。
 */
const POSE_MODEL_KEY = 'qwen_2_1_uc_i2i'
function selectPoseModel(): void {
  const pool = allModels.value.filter((m) => m.kind === 'i2i')
  const pick =
    pool.find((m) => m.uncensored && m.ready) ??
    pool.find((m) => m.uncensored) ??
    pool.find((m) => m.ready) ??
    pool[0]
  if (pick) {
    modelKey.value = pick.key
    pendingModelKey.value = ''
    return
  }
  pendingModelKey.value = POSE_MODEL_KEY
  pendingKind.value = 'i2i'
}

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

// ── 参考素材 id（逗号/空格分隔，1~10 个）——i2i 用 ──
const refIdsText = ref('')
const refIdsList = computed(() => splitIds(refIdsText.value))

// ── 姿势参考（换姿势）：单选，姿势图占一张参考额度 ──
// 为什么占额度：执行器把姿势图作为**最后一张参考图**上传，Qwen-Image 2.1 最多 10 张，
// 所以选了姿势后主体参考最多 9 张（后端同样会校验，前端提前拦住给出可读提示）。
const posePickerVisible = ref(false)
const selectedPose = ref<PoseItem | null>(null)
const maxRefs = computed(() => (selectedPose.value ? 9 : 10))
const refCountOk = computed(
  () => refIdsList.value.length >= 1 && refIdsList.value.length <= maxRefs.value,
)

/** 选中姿势：切到图生图**未审查版**档位（换姿势默认用第三个档位，见 selectPoseModel） */
function onPoseSelected(pose: PoseItem): void {
  selectedPose.value = pose
  selectPoseModel()
  Message.success(`已选姿势：${pose.name}`)
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

/** 插入一段「换姿势」描述模板（可再改）：明确参考图顺序，模型才不容易把主体也改掉 */
function insertPosePrompt(): void {
  const pose = selectedPose.value
  if (!pose) return
  const template = `保持第 1 张参考图的人物长相、发型与服装不变，按最后一张参考图的姿势（${pose.name}·${pose.angle}）重新生成，${poseFraming(pose.system)}。`
  // 连点两下不重复追加同一段
  if (prompt.value.includes(template)) return
  prompt.value = prompt.value.trim()
    ? `${prompt.value.trim()}
${template}`
    : template
}

/** 一键出图用的档位（未审查改款版）是否就绪：没权重时面板按钮要禁用并给原因 */
const quickModelReady = computed(() =>
  allModels.value.some((m) => m.key === POSE_MODEL_KEY && m.ready),
)

/** 一键出图：描述为空时自动补的通用姿势模板（姿势名/角度由姿势图本身决定，不用写死） */
const QUICK_PROMPT_TEMPLATE =
  '保持第 1 张参考图的人物长相、发型与服装不变，按最后一张参考图的姿势重新生成，全身照。'

/** 一键出图的每一轮：套用面板给的姿势/张数/步数 → 切未审查档位 → 复用正常提交流程 */
function onQuickRun(payload: { pose: PoseItem; count: number; steps: number }): void {
  count.value = payload.count
  steps.value = payload.steps
  selectedPose.value = payload.pose
  selectPoseModel()
  if (!prompt.value.trim()) prompt.value = QUICK_PROMPT_TEMPLATE

  // 防御：面板已禁用的场景（参考图被清空/档位未就绪）不该真的提交，否则会出一批错图
  if (kind.value !== 'i2i' || !refCountOk.value || !modelReady.value) {
    Message.warning('一键出图未启动：请确认已选参考图且图生图档位就绪')
    return
  }
  onSubmit()
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
    !!prompt.value.trim() &&
    !props.submitting &&
    !props.active &&
    modelReady.value &&
    (kind.value === 't2i' || refCountOk.value),
)

const activePreset = computed(() => sizePresets.find((p) => p.value === size.value))

function onSubmit() {
  if (!prompt.value.trim()) return
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
    pose_id: kind.value === 'i2i' && selectedPose.value ? selectedPose.value.id : undefined,
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
  // 历史姿势必须配图生图档位：历史模型缺失/不是 i2i 时按换姿势流程挑一个 i2i 档位
  if (
    payload.pose &&
    !(
      payload.model_key &&
      allModels.value.some((m) => m.key === payload.model_key && m.kind === 'i2i')
    )
  ) {
    selectPoseModel()
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
  // 换姿势流程的档位**最后**定：上面 initialRefIds 会把档位切到「就绪且非未审查」的
  // 第二个改款档位，必须在这之后再覆盖成未审查版，否则进入页面落在有审查档位上。
  if (props.initialPoseLib) selectPoseModel()
})
</script>

<template>
  <!-- 模型：始终可见。选中哪个档位就决定生成方式（文生图 / 参考图改款） -->
  <div class="gen-label">模型</div>
  <!-- 探测中且没有目录缓存：说明在等什么，而不是先闪两档假档位 -->
  <div v-if="!allModels.length" class="gen-hint gen-model-loading">
    正在读取模型档位…（本机要探测 ComfyUI 与权重文件，约 4 秒）
  </div>
  <a-radio-group v-else v-model="modelKey" size="small" direction="vertical" class="gen-models">
    <a-radio v-for="m in allModels" :key="m.key" :value="m.key">
      {{ shortModelLabel(m.label) }}
      <a-tag v-if="m.uncensored" color="red" size="small" class="gen-model-tag"> 未审查 </a-tag>
      <span class="gen-hint gen-model-note">
        {{ m.kind === 'i2i' ? '需参考图' : '纯文字' }}{{ m.ready ? '' : ' · 未就绪' }}
      </span>
    </a-radio>
  </a-radio-group>
  <div v-if="probing && allModels.length" class="gen-hint gen-model-loading">
    档位已列出（用上次的目录），就绪状态正在探测…
  </div>
  <a-alert v-if="currentModel?.uncensored" type="warning" class="gen-alert gen-alert--tight">
    {{ currentModel.notice || '该档位使用无内容安全过滤的权重。' }}
  </a-alert>

  <div v-if="kind === 'i2i'" class="gen-row">
    <a-input v-model="refIdsText" placeholder="参考素材 ID（1~10 个，逗号分隔）" allow-clear />
    <a-button @click="pickerVisible = true">选择图片</a-button>
    <span class="gen-hint">{{ refIdsList.length }}/{{ maxRefs }}</span>
  </div>
  <div v-if="kind === 'i2i'" class="gen-hint">
    参考图决定人物、场景与版型；描述要写「改完之后的样子」（例：「同一个人、同一场景，光脚不穿鞋」），
    只写命令（如「去掉鞋子」）容易失效。出图比例跟随参考图。当前
    {{ refIdsList.length }} 个参考（选姿势后上限 {{ maxRefs }} 张：姿势图占 1 张额度）。
  </div>

  <!-- 已选参考缩略图：写描述前先确认「选了哪几张、顺序对不对」，点任意一张看大图 -->
  <div v-if="kind === 'i2i' && (referenceItems?.length ?? 0) > 0" class="gen-ref-strip">
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

  <!-- 姿势参考（换姿势）：单选；执行器把它作为最后一张参考图上传 -->
  <div v-if="kind === 'i2i'" class="gen-pose">
    <div class="gen-row">
      <a-button @click="posePickerVisible = true">
        {{ selectedPose ? '更换姿势' : '选择姿势（换姿势）' }}
      </a-button>
      <a-button v-if="selectedPose" type="text" @click="insertPosePrompt">插入姿势描述</a-button>
      <a-button v-if="selectedPose" type="text" @click="clearPose">清除</a-button>
      <span v-if="!selectedPose" class="gen-hint">
        可选：从姿势图库挑一个姿势，让模特换成该姿势（个人本地使用）
      </span>
    </div>
    <div v-if="selectedPose" class="gen-pose-card">
      <img :src="selectedPose.thumb_url" :alt="selectedPose.name" loading="lazy" />
      <div class="gen-pose-meta">
        <div class="gen-pose-name">{{ selectedPose.name }} · {{ selectedPose.angle }}</div>
        <div class="gen-hint">
          {{ selectedPose.system }} / {{ selectedPose.category }} · 姿势图会作为<strong
            >最后一张</strong
          >
          参考图参与生成
        </div>
      </div>
    </div>
  </div>

  <!-- 一键出图：按「性感系 399」顺序轮播姿势，可连续多次；参数只有每次张数/步数/连续次数 -->
  <QuickPoseBatchPanel
    :platform-ready="ready"
    :model-ready="quickModelReady"
    :has-references="refIdsList.length > 0"
    :busy="submitting || active"
    @run="onQuickRun"
  />

  <div class="gen-label">描述（prompt）</div>
  <a-textarea
    v-model="prompt"
    placeholder="例：米色针织开衫配白色阔腿裤，极简工作室背景，柔和自然光，时尚编辑摄影"
    :auto-size="{ minRows: 4, maxRows: 8 }"
    allow-clear
  />

  <div class="gen-row">
    <a-input
      v-model="rewriteIdsText"
      placeholder="从素材反推：粘贴素材 ID（逗号分隔）"
      allow-clear
    />
    <a-button :loading="previewing" @click="onPreviewPrompt">反推</a-button>
  </div>
  <div class="gen-hint">反推会读取这些素材的标签、正文与主色拼成描述（可再手动改）。</div>

  <div class="gen-label">负向描述（可选）</div>
  <a-input v-model="negative" placeholder="例：模糊、变形、多余手指" allow-clear />

  <div class="gen-row gen-row--multi">
    <a-select v-model="size" :options="sizePresets" style="flex: 1" />
    <a-input-number v-model="steps" :min="1" :max="30" style="width: 110px">
      <template #prefix>步数</template>
    </a-input-number>
    <a-input-number v-model="count" :min="1" :max="4" style="width: 100px">
      <template #prefix>张数</template>
    </a-input-number>
  </div>
  <div class="gen-row">
    <a-input-number v-model="seed" :min="0" style="width: 180px">
      <template #prefix>seed</template>
    </a-input-number>
    <span class="gen-hint">0 = 随机（提交后会把真实 seed 显示在结果区，便于复现）</span>
  </div>

  <div class="gen-label">出图配方（保存当前参数，下次一键载入）</div>
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

  <div class="gen-actions">
    <a-button
      type="primary"
      :loading="submitting"
      :disabled="!canSubmit || !ready"
      @click="onSubmit"
    >
      生成 {{ count }} 张
    </a-button>
    <a-button v-if="active" status="warning" @click="emit('cancel')">取消</a-button>
    <span v-if="activePreset" class="gen-hint">{{ activePreset.label }}</span>
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
    @select="onPoseSelected"
  />
</template>

<style scoped>
/* 姿势参考卡片：缩略图 + 名称/角度（选中后显示，避免只留一个 id） */
.gen-pose {
  margin: 4px 0 10px;
}

.gen-pose-card {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px;
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  background: var(--color-fill-1);
}

.gen-pose-card img {
  width: 64px;
  height: 86px;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}

.gen-pose-name {
  color: var(--color-text-1);
  font-size: 13px;
  font-weight: 600;
}

/* 下面 .gen-alert / .gen-hint / .gen-label 三条在视图里另有一份：
   它们同时被环境提示、结果区与历史表用到，scoped 样式不能跨组件继承，故各自保留。 */
.gen-alert {
  margin-bottom: 12px;
}

/* 已选参考缩略图条：一行铺开，超出可横向滑动（10 张参考也不会把描述框挤下去） */
.gen-ref-strip {
  margin: 4px 0 10px;
}

.gen-ref-strip-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.gen-label--inline {
  margin-bottom: 0;
}

.gen-ref-thumbs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 2px;
}

.gen-ref-thumb {
  position: relative;
  flex: 0 0 auto;
  width: 64px;
  height: 64px;
  padding: 0;
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  overflow: hidden;
  background: var(--color-fill-2);
  cursor: zoom-in;
}

.gen-ref-thumb:hover {
  border-color: rgb(var(--primary-5));
}

.gen-ref-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.gen-ref-thumb-index {
  position: absolute;
  right: 2px;
  bottom: 2px;
  min-width: 14px;
  padding: 0 3px;
  border-radius: 3px;
  background: rgb(0 0 0 / 60%);
  color: #fff;
  font-size: 10px;
  line-height: 14px;
  text-align: center;
}
.gen-model-loading {
  padding: 4px 0 10px;
}
/* 紧贴在档位选择器下面的风险提示，不要额外的下边距把表单撑开 */
.gen-alert--tight {
  margin: 8px 0 0;
}
.gen-model-tag {
  margin-left: 4px;
}
.gen-models {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.gen-model-note {
  margin-left: 6px;
  font-size: 11px;
}
.gen-hint {
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}
.gen-label {
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.gen-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.gen-row--multi {
  flex-wrap: wrap;
}
.gen-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}
.gen-preset-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.gen-preset-tag {
  cursor: pointer;
}
</style>
