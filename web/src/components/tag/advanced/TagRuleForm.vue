<script setup lang="ts">
/** 批量编辑规则行：类型选择 + 动态参数 + 作用范围（本地副本编辑，变更通过 change 事件回传）。 */

import { reactive, ref, watch } from 'vue'
import { RULE_TYPE_LABELS, type BatchEditRule, type BatchEditScope } from '@/types/tagAdvanced'
import { CATEGORY_LABELS, SOURCE_LABELS } from '@/api/tags'

const props = defineProps<{ rule: BatchEditRule; index: number }>()
const emit = defineEmits<{ change: [rule: BatchEditRule]; remove: [] }>()

// 本地副本（避免直接修改 prop）
const local = reactive<BatchEditRule>({
  ...props.rule,
  scope: { ...(props.rule.scope ?? {}) },
})

watch(local, () => emit('change', { ...local, scope: { ...(local.scope ?? {}) } }), { deep: true })

// ── 作用范围编辑状态 ──
const SCOPE_TYPES = [
  { value: '', label: '全部标签' },
  { value: 'tag_ids', label: '指定标签 ID' },
  { value: 'category', label: '按类别' },
  { value: 'source', label: '按来源' },
  { value: 'search', label: '按名称关键词' },
]

function scopeTypeOf(scope: BatchEditScope | undefined): string {
  if (!scope) return ''
  if (scope.tag_ids?.length) return 'tag_ids'
  if (scope.category) return 'category'
  if (scope.source) return 'source'
  if (scope.search) return 'search'
  return ''
}

const scopeType = ref(scopeTypeOf(local.scope))
const tagIdsText = ref(local.scope?.tag_ids?.join(',') ?? '')
const categoryValue = ref(local.scope?.category ?? undefined)
const sourceValue = ref(local.scope?.source ?? undefined)
const searchValue = ref(local.scope?.search ?? '')

/** 把当前 scope 编辑状态写回本地副本 */
function syncScope() {
  let scope: BatchEditScope = {}
  if (scopeType.value === 'tag_ids') {
    scope = {
      tag_ids: tagIdsText.value
        .split(/[,，\s]+/)
        .map((s) => Number(s))
        .filter((n) => n > 0),
    }
  } else if (scopeType.value === 'category') {
    scope = { category: categoryValue.value }
  } else if (scopeType.value === 'source') {
    scope = { source: sourceValue.value }
  } else if (scopeType.value === 'search') {
    scope = { search: searchValue.value }
  }
  local.scope = scope
}

watch([scopeType, tagIdsText, categoryValue, sourceValue, searchValue], syncScope)

// ── 类型切换：重置参数为默认结构 ──
function onTypeChange() {
  const type = local.type
  if (type === 'regex_replace') {
    local.pattern = local.pattern ?? ''
    local.replacement = local.replacement ?? ''
  } else if (type === 'affix') {
    local.mode = local.mode ?? 'add_prefix'
    local.text = local.text ?? ''
  } else if (type === 'normalize') {
    local.ops = local.ops ?? ['fullwidth_to_halfwidth']
  } else {
    local.pattern = local.pattern ?? ''
    local.target_template = local.target_template ?? '$1'
  }
}
</script>

<template>
  <div class="rule-row">
    <div class="rule-head">
      <span class="rule-index">规则 {{ index + 1 }}</span>
      <a-select v-model="local.type" style="width: 150px" @change="onTypeChange">
        <a-option v-for="(label, key) in RULE_TYPE_LABELS" :key="key" :value="key">
          {{ label }}
        </a-option>
      </a-select>
      <a-button size="mini" status="danger" type="text" @click="emit('remove')">移除</a-button>
    </div>

    <div class="rule-params">
      <!-- 正则查找替换 -->
      <template v-if="local.type === 'regex_replace'">
        <a-input v-model="local.pattern" placeholder="正则（如 ^(.+)毛衣$）" style="width: 240px" />
        <a-input
          v-model="local.replacement"
          placeholder="替换为（支持 \1 捕获组）"
          style="width: 160px"
        />
      </template>
      <!-- 前后缀增删 -->
      <template v-else-if="local.type === 'affix'">
        <a-select v-model="local.mode" style="width: 140px">
          <a-option value="add_prefix">加前缀</a-option>
          <a-option value="remove_prefix">去前缀</a-option>
          <a-option value="add_suffix">加后缀</a-option>
          <a-option value="remove_suffix">去后缀</a-option>
        </a-select>
        <a-input v-model="local.text" placeholder="文本" style="width: 120px" />
      </template>
      <!-- 格式归一化 -->
      <template v-else-if="local.type === 'normalize'">
        <a-select v-model="local.ops" multiple style="width: 280px">
          <a-option value="fullwidth_to_halfwidth">全角转半角</a-option>
          <a-option value="trim">去首尾空白</a-option>
          <a-option value="dedup_chars">去连续重复字</a-option>
        </a-select>
      </template>
      <!-- 正则批量合并 -->
      <template v-else>
        <a-input v-model="local.pattern" placeholder="正则（捕获组）" style="width: 220px" />
        <a-input
          v-model="local.target_template"
          placeholder="目标模板（如 $1）"
          style="width: 140px"
        />
      </template>
    </div>

    <div class="rule-scope">
      <span class="scope-label">作用范围</span>
      <a-select v-model="scopeType" style="width: 130px">
        <a-option v-for="s in SCOPE_TYPES" :key="s.value" :value="s.value">
          {{ s.label }}
        </a-option>
      </a-select>
      <a-input
        v-if="scopeType === 'tag_ids'"
        v-model="tagIdsText"
        placeholder="标签 ID，逗号分隔"
        style="width: 220px"
      />
      <a-select
        v-else-if="scopeType === 'category'"
        v-model="categoryValue"
        placeholder="选择类别"
        style="width: 140px"
      >
        <a-option v-for="(label, key) in CATEGORY_LABELS" :key="key" :value="key">
          {{ label }}
        </a-option>
      </a-select>
      <a-select v-else-if="scopeType === 'source'" v-model="sourceValue" style="width: 140px">
        <a-option v-for="(label, key) in SOURCE_LABELS" :key="key" :value="key">
          {{ label }}
        </a-option>
      </a-select>
      <a-input
        v-else-if="scopeType === 'search'"
        v-model="searchValue"
        placeholder="名称包含关键词"
        style="width: 180px"
      />
    </div>
  </div>
</template>

<style scoped>
.rule-row {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rule-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rule-index {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  min-width: 44px;
}
.rule-params {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.rule-scope {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.scope-label {
  font-size: 12px;
  color: #9ca3af;
}
</style>
