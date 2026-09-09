<script setup lang="ts">
/**
 * AI 打标纠错反馈弹窗：素材详情页「标错了」与「补充漏标」共用。
 *
 * - mode='wrong'：对指定标签反馈「AI 多标 / 类别错 / 名称不规范」，
 *   多标会同时删除该素材上该标签的关联；
 * - mode='missing'：补充 AI 漏掉的标签（可指定类别），提交后补建关联。
 *
 * 两类反馈都会落库 tag_corrections，供提示词版本质量看板统计纠错率。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import {
  recordTagCorrection,
  type TagCorrectionReason,
  type TagCorrectionResult,
} from '@/api/inspirations'

const props = defineProps<{
  visible: boolean
  inspirationId: string
  /** wrong=反馈已有标签标错；missing=补充 AI 漏标 */
  mode: 'wrong' | 'missing'
  /** wrong 模式下被反馈的标签名 */
  tagName?: string
  /** wrong 模式下标签所属类别（仅用于展示） */
  tagCategory?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'recorded', result: TagCorrectionResult): void
}>()

/** 反馈原因选项（wrong 模式） */
const WRONG_REASONS: { value: TagCorrectionReason; label: string; desc: string }[] = [
  { value: 'multi', label: 'AI 多标', desc: '图里并没有这个单品/特征，会同时删除该标签关联' },
  { value: 'wrong_category', label: '类别错', desc: '标签本身对，但归错了类别（仅记录）' },
  { value: 'bad_name', label: '名称不规范', desc: '命名不符合口径（如缺颜色/长度，仅记录）' },
]

/** 可选的补标类别（与标签类别体系一致） */
const CATEGORY_OPTIONS = [
  { value: 'item_type', label: '单品类型' },
  { value: 'color', label: '颜色' },
  { value: 'style', label: '风格' },
  { value: 'design_detail', label: '款式细节' },
  { value: 'material', label: '面料材质' },
  { value: 'fit', label: '版型' },
  { value: 'attribute', label: '图片属性' },
  { value: 'atmosphere', label: '环境氛围' },
  { value: 'expression', label: '模特表情' },
  { value: 'leg_posture', label: '腿部姿态' },
  { value: 'outfit', label: '穿搭大标签' },
  { value: 'free', label: '自由标签' },
]

const reason = ref<TagCorrectionReason>('multi')
const missingName = ref('')
const missingCategory = ref('item_type')
const note = ref('')
const submitting = ref(false)

const title = computed(() => (props.mode === 'wrong' ? '反馈「标错了」' : '补充 AI 漏标的标签'))

/** 打开弹窗时重置表单（wrong 模式默认「AI 多标」） */
watch(
  () => props.visible,
  (v) => {
    if (!v) return
    reason.value = 'multi'
    missingName.value = ''
    missingCategory.value = 'item_type'
    note.value = ''
  },
)

/** 当前提交的标签名：wrong 模式用被反馈标签，missing 模式用输入值 */
const targetName = computed(() =>
  props.mode === 'wrong' ? props.tagName || '' : missingName.value.trim(),
)

const canSubmit = computed(() => !!targetName.value && !submitting.value)

async function submit() {
  if (!canSubmit.value) {
    Message.warning(props.mode === 'missing' ? '请填写要补充的标签名' : '标签名为空')
    return
  }
  submitting.value = true
  try {
    const result = await recordTagCorrection(props.inspirationId, {
      tag_name: targetName.value,
      reason: props.mode === 'missing' ? 'missing' : reason.value,
      note: note.value.trim() || undefined,
      category: props.mode === 'missing' ? missingCategory.value : undefined,
    })
    if (result.action === 'removed') {
      Message.success(`已记录反馈并移除标签「${result.tag_name}」`)
    } else if (result.action === 'added') {
      Message.success(`已记录反馈并补充标签「${result.tag_name}」`)
    } else {
      Message.success('已记录反馈（标签本身未改动）')
    }
    emit('recorded', result)
    emit('update:visible', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '提交反馈失败'))
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="title"
    :width="480"
    :ok-loading="submitting"
    :ok-button-props="{ disabled: !canSubmit }"
    ok-text="提交反馈"
    @ok="submit"
    @cancel="close"
  >
    <!-- 被反馈的标签（wrong 模式） -->
    <div v-if="mode === 'wrong'" class="target-tag">
      标签：<strong>{{ tagName }}</strong>
      <span v-if="tagCategory" class="tag-category">（{{ tagCategory }}）</span>
    </div>

    <!-- 漏标：输入标签名 + 类别 -->
    <template v-else>
      <div class="field">
        <div class="field-label">要补充的标签名 <span class="required">*</span></div>
        <a-input
          v-model="missingName"
          placeholder="如：黑色连裤袜、藏青格纹百褶短裙"
          :max-length="64"
          allow-clear
        />
      </div>
      <div class="field">
        <div class="field-label">标签类别</div>
        <a-select v-model="missingCategory" :options="CATEGORY_OPTIONS" />
      </div>
    </template>

    <!-- 原因（wrong 模式） -->
    <div v-if="mode === 'wrong'" class="field">
      <div class="field-label">问题类型</div>
      <a-radio-group v-model="reason" direction="vertical">
        <a-radio v-for="item in WRONG_REASONS" :key="item.value" :value="item.value">
          {{ item.label }}
          <span class="reason-desc">{{ item.desc }}</span>
        </a-radio>
      </a-radio-group>
    </div>

    <div class="field">
      <div class="field-label">备注（可选）</div>
      <a-textarea
        v-model="note"
        placeholder="补充说明，例如「图里是长筒袜不是过膝袜」"
        :max-length="500"
        :auto-size="{ minRows: 2, maxRows: 4 }"
        show-word-limit
      />
    </div>

    <div class="hint">
      反馈会记录到纠错库，用于统计各提示词版本的打标质量；「AI 多标 / 漏标」会立即调整该素材标签。
    </div>
  </a-modal>
</template>

<style scoped>
.target-tag {
  font-size: 13px;
  color: var(--color-text-2);
}
.tag-category {
  color: var(--color-text-3);
}
.field {
  margin-top: 14px;
}
.field-label {
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--color-text-2);
}
.required {
  color: rgb(var(--danger-6));
}
.reason-desc {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-text-3);
}
.hint {
  margin-top: 14px;
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.6;
}
</style>
