/** 出图参数面板的模型档位选择：档位表三态、默认挑选、姿势/文生图切换。
 *
 * 从 GenerateParamForm.vue 抽出（该组件 1,000+ 行）；面板仍持有表单状态，
 * 这里只负责「选哪个档位」这条流程。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { GenerateStatus } from '@/api/generate'

/** 档位选项。不 export：只在本模块内使用（ts-prune 不追踪 .vue 引用，导出会被判死代码）。 */
interface ModelOption {
  key: string
  label: string
  kind: 't2i' | 'i2i'
  uncensored: boolean
  notice: string
  ready: boolean
  provider: string
  costPerImage: number
  requiresUpload: boolean
  supportsSeed: boolean
  supportsNegative: boolean
  /** 云端一次最多可选姿态数（本地档位固定 10，仅作占位） */
  maxPoses: number
  /** 参考素材张数上限（云端只能是 1 张素材图） */
  maxRefs: number
}

interface ModelSelectionProps {
  status: GenerateStatus | null
  modelsMetaFallback?: GenerateStatus['models_meta'] | null
  initialModelKey?: string
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
    provider: 'comfyui',
    costPerImage: 0,
    requiresUpload: false,
    supportsSeed: true,
    supportsNegative: true,
    maxPoses: 10,
    maxRefs: 10,
  },
  {
    key: 'qwen_2_1_i2i',
    label: 'Qwen-Image 2.1（参考图改款）',
    kind: 'i2i',
    uncensored: false,
    notice: '',
    ready: true,
    provider: 'comfyui',
    costPerImage: 0,
    requiresUpload: false,
    supportsSeed: true,
    supportsNegative: true,
    maxPoses: 10,
    maxRefs: 10,
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
    provider: m.provider || 'comfyui',
    costPerImage: Number(m.cost_per_image_cny ?? 0),
    requiresUpload: m.requires_upload === true,
    supportsSeed: m.supports_seed !== false,
    supportsNegative: m.supports_negative !== false,
    maxPoses: Number(m.max_poses ?? 10),
    maxRefs: Number(m.max_refs ?? 10),
  }))
}

const POSE_MODEL_KEY = 'qwen_2_1_uc_i2i'
const POSE_CLOUD_MODEL_KEY = 'seedream_5_0_flash'
const POSE_TEXT_MODEL_KEY = 'z_image_int8'

export function useGenerateModelSelection(props: ModelSelectionProps) {
  /** 全部可选档位：三态（真实目录 / 上次缓存 / 旧后端兜底） */
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
  /** 外部显式请求的档位（只生效一次） */
  const pendingModelKey = ref((props.initialModelKey ?? '').trim())
  /** 「想要图生图」的待认领标记：档位表未到时先记下意图 */
  const pendingKind = ref<'t2i' | 'i2i' | null>(null)

  /** 默认档位：优先就绪且非未审查；外部显式请求优先认领 */
  watch(
    allModels,
    (list) => {
      if (!list.length) return
      const wanted = pendingModelKey.value
      if (wanted) {
        const hit = list.find((m) => m.key === wanted)
        if (hit) {
          pendingModelKey.value = ''
          modelKey.value = hit.key
          Message.info(`已切到「${hit.label}」，参考素材 ID 已填好`)
          return
        }
      }
      if (pendingKind.value) {
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

  /** 按「本地 / 云端」分组 */
  const modelGroups = computed(() => {
    const local = allModels.value.filter((m) => m.provider !== 'ark')
    const cloud = allModels.value.filter((m) => m.provider === 'ark')
    return [
      ...(local.length ? [{ key: 'local', label: '本地 ComfyUI', models: local }] : []),
      ...(cloud.length ? [{ key: 'cloud', label: '云端（素材会上传）', models: cloud }] : []),
    ]
  })

  /** 卡片标题用「任务」而不是模型名 */
  function modelTaskTitle(m: ModelOption): string {
    if (m.provider === 'ark') return '云端换姿势'
    if (m.uncensored) return '未审查改款'
    return m.kind === 't2i' ? '文生图' : '参考图改款'
  }

  function modelTaskDesc(m: ModelOption): string {
    if (m.provider === 'ark') return '素材图 + 姿态图，上传云端'
    if (m.uncensored) return '无内容过滤的参考图改款'
    return m.kind === 't2i' ? '只写描述，从零生成' : '用现有素材改服装/场景'
  }

  const isArk = computed(() => currentModel.value?.provider === 'ark')
  const supportsSeed = computed(() => currentModel.value?.supportsSeed !== false)
  const supportsNegative = computed(() => currentModel.value?.supportsNegative !== false)
  const requiresUpload = computed(() => currentModel.value?.requiresUpload === true)
  const kind = computed<'t2i' | 'i2i'>(() => currentModel.value?.kind ?? 't2i')
  const modelReady = computed(() => currentModel.value?.ready ?? false)
  const i2iReady = computed(() => allModels.value.some((m) => m.kind === 'i2i' && m.ready))

  function selectModelByKind(target: 't2i' | 'i2i'): void {
    const pool = allModels.value.filter((m) => m.kind === target)
    const pick = pool.find((m) => m.ready && !m.uncensored) ?? pool.find((m) => m.ready) ?? pool[0]
    if (pick) modelKey.value = pick.key
  }

  /** 换姿势默认走未审查版改款档位；档位表未到时记下意图 */
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

  /** 姿势生成方式：默认线稿参考（图生图），可切文字描述（文生图） */
  const poseMode = ref<'text' | 'lineart'>('lineart')

  function selectPoseTextModel(): void {
    const pool = allModels.value.filter((m) => m.kind === 't2i')
    const pick = pool.find((m) => m.ready && !m.uncensored) ?? pool.find((m) => m.ready) ?? pool[0]
    if (pick) {
      modelKey.value = pick.key
      pendingModelKey.value = ''
      return
    }
    pendingModelKey.value = POSE_TEXT_MODEL_KEY
    pendingKind.value = 't2i'
  }

  function applyPoseMode(mode: 'text' | 'lineart'): void {
    poseMode.value = mode
    if (mode === 'text') selectPoseTextModel()
    else selectPoseModel()
  }

  return {
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
    selectPoseModel,
    selectPoseTextModel,
    applyPoseMode,
    poseMode,
    POSE_MODEL_KEY,
    POSE_CLOUD_MODEL_KEY,
    POSE_TEXT_MODEL_KEY,
  }
}
