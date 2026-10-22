<script setup lang="ts">
/** 本地出图页：填参数 → 生成 → 候选区勾选 → 确认入库。
 *
 * 为什么两段式（生成 / 入库分开）：本项目一贯「宁缺毋滥、手动确认」——
 * 生成 4 张里通常只要 1~2 张，直接入库会让废图占库容、进统计、还进打标队列烧算力。
 *
 * 显存现实（实测）：16 GB 卡上 1024² 峰值已到 15.1 GiB，所以尺寸只给预设、张数上限 4，
 * 且生成时会与打标任务互斥（后端 GPU 互斥组），必要时排队等 Ollama 释放显存。
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { useImageGen } from '@/composables/useImageGen'
import { formatDateTime } from '@/utils/format'
import ReferencePickerModal from '@/components/generate/ReferencePickerModal.vue'
import { discardRun } from '@/api/generate'
import { getApiErrorMessage } from '@/utils/apiError'
import { useGenerationPresets, type GenerationPreset } from '@/composables/useGenerationPresets'

const route = useRoute()
/** 删除某条生成记录（连同批次目录里的候选图）；已入库的素材不受影响 */
const deletingId = ref<number | null>(null)

async function removeRun(id: number) {
  deletingId.value = id
  try {
    const r = await discardRun(id)
    Message.success(`已删除（清理 ${r.removed_files ?? 0} 个文件）`)
    // 删掉的正是当前打开的批次：清空详情区，否则用户还能对已删除的批次点「确认入库」
    if (run.value?.id === id) reset()
    await loadHistory()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
  } finally {
    deletingId.value = null
  }
}

const {
  status,
  statusLoading,
  submitting,
  run,
  candidates,
  selected,
  active,
  stageText,
  lastSeed,
  reset,
  history,
  historyLoading,
  loadStatus,
  submit,
  toggle,
  selectAll,
  clearSelection,
  confirm,
  cancel,
  promptFrom,
  discard,
  stopPoll,
  loadHistory,
  retry,
  regenerate,
} = useImageGen()

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

// ── 模型档位（一个档位就决定生成方式：文生图 / 参考图改款）──
/** 档位选项。 */
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

/** 全部可选档位：跟着后端 `MODEL_SPECS` 走，加一个权重只改后端、前端不用发版。 */
const allModels = computed<ModelOption[]>(() => {
  const meta = status.value?.models_meta ?? {}
  const models = status.value?.models ?? {}
  const list = Object.entries(meta).map<ModelOption>(([key, m]) => ({
    key,
    label: m.label,
    kind: m.kind === 'i2i' ? 'i2i' : 't2i',
    uncensored: m.uncensored === true,
    notice: m.notice || '',
    ready: models[key] === 'ok',
  }))
  if (list.length) return list
  return LEGACY_MODELS.map((m) => ({ ...m, ready: !status.value || models[m.key] === 'ok' }))
})

/** 当前选中的档位 */
const modelKey = ref('')

/** 默认档位：优先**就绪**且**非未审查**的。
 *
 * 未审查版必须由用户主动点选 —— 默认选中「无内容过滤」的权重，等于替用户做了一次
 * 他自己不知道的风险选择。已选中的档位仍然存在时不动它（刷新环境不重置用户选择）。
 */
watch(
  allModels,
  (list) => {
    if (!list.length) return
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

/** 是否有可用的参考图改款档位（配方载入、深链跳转要用） */
const i2iReady = computed(() => allModels.value.some((m) => m.kind === 'i2i' && m.ready))

/** 按钮上显示的短名：去掉 label 括号里的耗时说明 */
function shortLabel(label: string): string {
  return label.split('（')[0] ?? label
}

/** 按生成方式挑一个档位（配方载入 / 从素材详情跳转用）：优先就绪且非未审查 */
function selectModelByKind(target: 't2i' | 'i2i'): void {
  const pool = allModels.value.filter((m) => m.kind === target)
  const pick = pool.find((m) => m.ready && !m.uncensored) ?? pool.find((m) => m.ready) ?? pool[0]
  if (pick) modelKey.value = pick.key
}

/** 档位 key → 展示名（历史表用；后端没给档位表时退回 key，不显示空白） */
function modelLabel(key: string): string {
  const meta = status.value?.models_meta?.[key]
  return meta ? shortLabel(meta.label) : key || '—'
}

/** 该档位是否为未审查权重（历史表打标用） */
function modelUncensored(key: string): boolean {
  return status.value?.models_meta?.[key]?.uncensored === true
}

/** 参考素材 id（逗号/空格分隔，1~10 个）——i2i 用 */
const refIdsText = ref('')
const refIdsList = computed(() => splitIds(refIdsText.value))
const refCountOk = computed(() => refIdsList.value.length >= 1 && refIdsList.value.length <= 10)

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
    const text = await promptFrom(ids)
    if (text) prompt.value = text
  } finally {
    previewing.value = false
  }
}

// ── 生成与入库 ──
async function onSubmit() {
  if (!prompt.value.trim()) return
  await submit({
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
  })
}

const ready = computed(() => status.value?.ready === true)

/** 历史刷新：批次进入终态时拉一次，避免用户手动刷新页面 */
watch(
  () => run.value?.status,
  (st) => {
    if (st && ['done', 'failed', 'cancelled'].includes(st)) void loadHistory()
  },
)

const canSubmit = computed(
  () =>
    !!prompt.value.trim() &&
    !submitting.value &&
    !active.value &&
    modelReady.value &&
    (kind.value === 't2i' || refCountOk.value),
)

const activePreset = computed(() => sizePresets.find((p) => p.value === size.value))
const selectedCount = computed(() => selected.value.size)

onMounted(() => {
  // 从素材详情「用这张图改款」跳进来：预填参考图并切到改款档位
  const refParam = route.query.ref
  if (typeof refParam === 'string' && refParam.trim()) {
    refIdsText.value = refParam.trim()
    selectModelByKind('i2i')
  }
  void loadStatus()
  void loadHistory()
})
onUnmounted(stopPoll)
</script>

<template>
  <div class="gen-page">
    <div class="gen-head">
      <div>
        <h2 class="gen-title">本地出图</h2>
        <div class="gen-sub">
          用本机 ComfyUI 生成穿搭图；生成结果先进候选区，勾选后才入库（默认待审核、标记「AI
          生成」）。
        </div>
      </div>
      <a-button :loading="statusLoading" @click="loadStatus">刷新环境</a-button>
    </div>

    <!-- 环境未就绪：给可执行引导，而不是让用户点了才失败 -->
    <a-alert v-if="status && !ready" type="warning" class="gen-alert">
      {{ status.reason || '出图环境未就绪' }}
      <div class="gen-hint">
        启动方式：双击 <code>D:\AI\ComfyUI\run_nvidia_gpu.bat</code>，等浏览器能打开
        <code>http://127.0.0.1:8188</code> 后点「刷新环境」。
      </div>
    </a-alert>
    <a-alert v-else-if="status" type="success" class="gen-alert">
      ComfyUI {{ status.comfyui.version }} 就绪 · 显存 {{ status.comfyui.vram_free_gib }} /
      {{ status.comfyui.vram_total_gib }} GiB 可用
      <span v-if="status.ollama_resident.length" class="gen-hint">
        · Ollama 占用：{{
          status.ollama_resident.map((m) => m.name).join('、')
        }}（出图会等待其释放）
      </span>
    </a-alert>

    <div class="gen-grid">
      <!-- 左：参数 -->
      <a-card class="gen-card" :bordered="false">
        <!-- 模型：始终可见。选中哪个档位就决定生成方式（文生图 / 参考图改款） -->
        <div class="gen-label">模型</div>
        <a-radio-group v-model="modelKey" size="small" direction="vertical" class="gen-models">
          <a-radio v-for="m in allModels" :key="m.key" :value="m.key">
            {{ shortLabel(m.label) }}
            <a-tag v-if="m.uncensored" color="red" size="small" class="gen-model-tag">
              未审查
            </a-tag>
            <span class="gen-hint gen-model-note">
              {{ m.kind === 'i2i' ? '需参考图' : '纯文字' }}{{ m.ready ? '' : ' · 未就绪' }}
            </span>
          </a-radio>
        </a-radio-group>
        <a-alert v-if="currentModel?.uncensored" type="warning" class="gen-alert gen-alert--tight">
          {{ currentModel.notice || '该档位使用无内容安全过滤的权重。' }}
        </a-alert>

        <div v-if="kind === 'i2i'" class="gen-row">
          <a-input
            v-model="refIdsText"
            placeholder="参考素材 ID（1~10 个，逗号分隔）"
            allow-clear
          />
          <a-button @click="pickerVisible = true">选择图片</a-button>
          <span class="gen-hint">{{ refIdsList.length }}/10</span>
        </div>
        <div v-if="kind === 'i2i'" class="gen-hint">
          参考图决定「主体与版型」，描述写「要改成什么」。当前 {{ refIdsList.length }} 个参考。
        </div>

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
          <a-button v-if="active" status="warning" @click="cancel">取消</a-button>
          <span v-if="activePreset" class="gen-hint">{{ activePreset.label }}</span>
        </div>
      </a-card>

      <!-- 右：进度与候选 -->
      <a-card class="gen-card" :bordered="false">
        <div class="gen-result-head">
          <span class="gen-label" style="margin: 0">生成结果</span>
          <span v-if="lastSeed" class="gen-hint">本次 seed 基值 {{ lastSeed }}</span>
        </div>

        <div v-if="!run" class="gen-empty">还没有生成结果：左侧填好描述后点「生成」。</div>
        <template v-else>
          <a-alert
            :type="run.status === 'failed' ? 'error' : run.status === 'done' ? 'success' : 'info'"
            class="gen-alert"
          >
            {{ stageText }}
          </a-alert>

          <div v-if="run.status === 'done' && candidates.length" class="gen-toolbar">
            <a-button size="mini" @click="selectAll">全选未入库</a-button>
            <a-button size="mini" @click="clearSelection">清空选择</a-button>
            <span class="gen-hint">已选 {{ selectedCount }} / {{ candidates.length }}</span>
            <a-button type="primary" size="mini" :disabled="selectedCount === 0" @click="confirm">
              确认入库
            </a-button>
            <a-button size="mini" status="danger" @click="discard">丢弃本批</a-button>
          </div>

          <div v-if="candidates.length" class="gen-candidates">
            <div
              v-for="item in candidates"
              :key="item.filename"
              class="gen-item"
              :class="{ 'is-selected': selected.has(item.filename) }"
              @click="!item.imported && toggle(item.filename)"
            >
              <img :src="item.url" :alt="item.filename" loading="lazy" />
              <div class="gen-item-meta">
                <span>seed {{ item.seed }}</span>
                <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
                <a-button
                  v-if="!item.imported"
                  size="mini"
                  type="text"
                  :loading="submitting"
                  title="用新 seed 重出这一张"
                  @click.stop="regenerate(item)"
                >
                  换 seed
                </a-button>
              </div>
            </div>
          </div>
          <div v-else-if="run.status === 'done'" class="gen-empty">
            本批没有产出（可能已丢弃或全部失败）。
          </div>
        </template>
      </a-card>
    </div>

    <ReferencePickerModal
      v-model:visible="pickerVisible"
      :selected-ids="refIdsList"
      :max="10"
      @confirm="onPickConfirm"
    />

    <!-- 生成历史：同参重跑入口（历史记录只存参数，图在候选区，过期会被清理） -->
    <a-card class="gen-card gen-history" :bordered="false">
      <div class="gen-result-head">
        <span class="gen-label" style="margin: 0">生成历史</span>
        <a-button size="mini" :loading="historyLoading" @click="loadHistory">刷新</a-button>
      </div>
      <div v-if="!history.length" class="gen-empty">还没有生成记录。</div>
      <a-table
        v-else
        :data="history"
        :pagination="false"
        row-key="id"
        size="small"
        :scroll="{ x: 960 }"
      >
        <template #columns>
          <a-table-column title="#" data-index="id" :width="70" />
          <a-table-column title="描述" data-index="prompt" :width="320" ellipsis />
          <a-table-column title="模型" :width="190">
            <template #cell="{ record }">
              <span class="gen-model-cell">
                {{ modelLabel(record.model_key) }}
                <a-tag v-if="modelUncensored(record.model_key)" color="red" size="small">
                  未审查
                </a-tag>
              </span>
            </template>
          </a-table-column>
          <a-table-column title="状态" :width="110">
            <template #cell="{ record }">
              {{ record.status }}（{{ record.done }}/{{ record.total }}）
            </template>
          </a-table-column>
          <a-table-column title="时间" :width="150">
            <template #cell="{ record }">
              <span style="white-space: nowrap">{{ formatDateTime(record.created_at) }}</span>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="210">
            <template #cell="{ record }">
              <a-button size="mini" :loading="submitting" @click="retry(record.id)">
                同参重跑
              </a-button>
              <!-- 只对已结束的批次给删除：运行中的批次后端会拒绝（400），按钮不显示更清楚 -->
              <a-popconfirm
                v-if="['done', 'failed', 'cancelled'].includes(record.status)"
                content="删除这条生成记录及其候选图？（只清理本批次的候选文件）"
                ok-text="删除"
                @ok="removeRun(record.id)"
              >
                <a-button
                  size="mini"
                  status="danger"
                  style="margin-left: 6px"
                  :loading="deletingId === record.id"
                >
                  删除
                </a-button>
              </a-popconfirm>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.gen-page {
  padding: 16px;
}
.gen-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.gen-title {
  margin: 0 0 4px;
  font-size: 18px;
}
.gen-sub {
  color: #8a8f99;
  font-size: 12px;
}
.gen-alert {
  margin-bottom: 12px;
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
.gen-model-cell {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.gen-hint {
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}
.gen-hint code {
  padding: 0 3px;
  border-radius: 3px;
  background: #f2f3f5;
}
.gen-grid {
  display: grid;
  grid-template-columns: minmax(320px, 420px) 1fr;
  gap: 12px;
}
@media (max-width: 900px) {
  .gen-grid {
    grid-template-columns: 1fr;
  }
}
.gen-card {
  background: #fff;
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
.gen-result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
.gen-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.gen-candidates {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.gen-item {
  border: 2px solid transparent;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  background: #f7f8fa;
}
.gen-item:hover {
  border-color: #94bfff;
}
.gen-item.is-selected {
  border-color: #165dff;
}
.gen-item img {
  width: 100%;
  display: block;
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
.gen-history {
  margin-top: 12px;
}
.gen-item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px;
  font-size: 11px;
  color: #8a8f99;
}
</style>
