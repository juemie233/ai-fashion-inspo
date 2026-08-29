<script setup lang="ts">
/** 多模型 × 多提示词组合分析卡片：一次提交生成全部「模型 × 提示词」组合分析任务。 */
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import type { PromptOption, MultiAnalyzeParams } from '@/types/analysis'

/** 与后端约定的一致：组合数上限（超出时禁止提交） */
const MAX_COMBINATIONS = 10

const props = defineProps<{
  /** 是否有分析任务正在提交（父级批量分析状态复用） */
  submitting: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', params: MultiAnalyzeParams): void
}>()

/** 已安装的 Ollama 模型名列表（排除嵌入模型） */
const modelOptions = ref<string[]>([])
/** 当前默认视觉模型（后端 settings.ollama_vision_model，用于默认勾选） */
const activeModel = ref('')
/** 可选提示词列表（当前默认 + 历史保存版本） */
const promptOptions = ref<PromptOption[]>([])

const selectedModels = ref<string[]>([])
const selectedPromptIds = ref<number[]>([0])
/** 是否把标签合并到素材（默认关闭：组合分析结果只存历史，人工对比后手动应用） */
const applyTags = ref(false)
const loading = ref(false)

/** 组合数 = 模型数 × 提示词数（未选择时后端回退默认模型 / 默认提示词） */
const combinationCount = computed(
  () => Math.max(selectedModels.value.length, 1) * Math.max(selectedPromptIds.value.length, 1),
)

const tooManyCombinations = computed(() => combinationCount.value > MAX_COMBINATIONS)

/** 加载已安装模型与可选提示词 */
async function loadOptions() {
  loading.value = true
  try {
    const [modelsRes, promptsRes] = await Promise.all([
      apiClient.get<{
        models: Array<{ name: string; is_embedding: boolean; is_active: boolean }>
        active_model: string
      }>('/ai/models'),
      apiClient.get<{ model: string; options: PromptOption[] }>('/ai/prompt-options'),
    ])
    modelOptions.value = modelsRes.data.models.filter((m) => !m.is_embedding).map((m) => m.name)
    activeModel.value = promptsRes.data.model || modelsRes.data.active_model
    promptOptions.value = promptsRes.data.options
    // 默认勾选当前默认视觉模型 + 当前默认提示词
    if (activeModel.value && modelOptions.value.includes(activeModel.value)) {
      selectedModels.value = [activeModel.value]
    }
  } catch {
    Message.error('加载模型 / 提示词选项失败，请确认 Ollama 已启动')
  } finally {
    loading.value = false
  }
}

/** 提交组合分析（素材范围沿用「全部未分析」逻辑，由父级触发） */
function submit() {
  if (tooManyCombinations.value) {
    Message.warning(`组合数过多（${combinationCount.value}），最多支持 ${MAX_COMBINATIONS} 个组合`)
    return
  }
  emit('submit', {
    models: selectedModels.value,
    promptIds: selectedPromptIds.value,
    applyTags: applyTags.value,
  })
}

onMounted(loadOptions)
</script>

<template>
  <a-card title="多模型 × 多提示词组合分析" size="small" style="margin-bottom: 16px">
    <a-spin :loading="loading" style="display: block">
      <div class="multi-analyze-form">
        <div class="form-row">
          <span class="form-label">视觉模型</span>
          <a-select
            v-model="selectedModels"
            :options="modelOptions.map((m) => ({ label: m, value: m }))"
            multiple
            placeholder="勾选要对比的模型（可多选，不选则用默认模型）"
            size="small"
            style="flex: 1; min-width: 260px"
            :max-tag-count="3"
            allow-clear
          />
        </div>
        <div class="form-row">
          <span class="form-label">提示词</span>
          <a-select
            v-model="selectedPromptIds"
            :options="promptOptions.map((o) => ({ label: o.label, value: o.id }))"
            multiple
            placeholder="勾选要对比的 Prompt 版本（可多选，含当前默认提示词）"
            size="small"
            style="flex: 1; min-width: 260px"
            :max-tag-count="3"
            allow-clear
          />
        </div>
        <div class="form-row">
          <span class="form-label">分析范围</span>
          <span class="form-tip">全部未分析图片素材（与「分析全部未分析」口径一致）</span>
        </div>
        <div class="form-row">
          <span class="form-label">应用标签</span>
          <a-checkbox v-model="applyTags">分析完成后把标签合并到素材</a-checkbox>
          <span class="form-tip">
            默认关闭：结果只写入分析历史，不改动素材标签，可在历史中对比后手动「应用到素材」
          </span>
        </div>
        <div class="form-row submit-row">
          <a-tag :color="tooManyCombinations ? 'red' : 'arcoblue'" size="small">
            组合数：{{ combinationCount }}（模型 {{ Math.max(selectedModels.length, 1) }} × 提示词
            {{ Math.max(selectedPromptIds.length, 1) }}）
          </a-tag>
          <span v-if="tooManyCombinations" class="form-tip warn">
            最多支持 {{ MAX_COMBINATIONS }} 个组合
          </span>
          <a-button
            type="primary"
            size="small"
            :loading="submitting"
            :disabled="tooManyCombinations"
            @click="submit"
          >
            提交组合分析
          </a-button>
        </div>
      </div>
    </a-spin>
  </a-card>
</template>

<style scoped>
.multi-analyze-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.form-label {
  font-size: 13px;
  color: #4e5969;
  width: 60px;
  flex-shrink: 0;
}
.form-tip {
  font-size: 12px;
  color: #86909c;
}
.form-tip.warn {
  color: #f53f3f;
}
.submit-row {
  justify-content: flex-end;
}
</style>
