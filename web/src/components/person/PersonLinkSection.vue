<script setup lang="ts">
/** 素材详情「关联人物」区块：展示已关联的博主/模特，支持从已有列表中选择添加与解除关联。
 *
 * 博主与模特已拆分为独立关联（inspiration_bloggers / inspiration_models），
 * 本组件按 kind 对接对应 API。关联一律使用 ID（不按名称匹配），规避同名多人歧义。
 * 添加方式为下拉选择框：挂载时分页拉取该种类全部人物（后端单页 size 上限 200），
 * 已关联人物从候选中排除。支持模糊搜索 / 多关键字匹配（空格分隔关键字，全部命中才显示）。
 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { bloggersApi, modelsApi } from '@/api/persons'
import type { PersonBrief } from '@shared/types/person'

const props = defineProps<{
  /** 人物种类：blogger（穿搭博主）/ model（职业模特） */
  kind: 'blogger' | 'model'
  /** 当前素材已关联的人物 */
  persons: PersonBrief[]
  /** 素材 ID */
  inspirationId: string
}>()

const emit = defineEmits<{
  (e: 'change', persons: PersonBrief[]): void
}>()

/** 按种类选择 API */
const api = props.kind === 'blogger' ? bloggersApi : modelsApi
const kindLabel = props.kind === 'blogger' ? '穿搭博主' : '职业模特'

/** 全部人物（分页拉取，作为下拉候选池） */
const allPersons = ref<PersonBrief[]>([])
/** 是否正在加载人物列表 */
const loading = ref(false)
/** 当前选中的候选人物 ID */
const selectedId = ref<number>()
/** 搜索框当前输入（受控，关联成功后清空，避免残留关键字） */
const searchText = ref('')

/** 下拉候选选项：排除已关联人物 */
const selectOptions = computed(() => {
  const linkedIds = new Set(props.persons.map((p) => p.id))
  return allPersons.value
    .filter((p) => !linkedIds.has(p.id))
    .map((p) => ({ label: p.name, value: p.id }))
})

/** 模糊匹配单个关键字：字符须按顺序出现在文本中（子序列匹配，忽略大小写） */
function fuzzyMatch(text: string, keyword: string): boolean {
  let index = 0
  const lowerText = text.toLowerCase()
  for (const char of keyword.toLowerCase()) {
    index = lowerText.indexOf(char, index)
    if (index === -1) return false
    index += 1
  }
  return true
}

/** 下拉关键字过滤：空格分隔多关键字，全部命中才显示（支持模糊子序列匹配） */
function filterOption(inputValue: string, option: { label?: string }) {
  const keywords = inputValue.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (keywords.length === 0) return true
  const label = option.label ?? ''
  return keywords.every((keyword) => fuzzyMatch(label, keyword))
}

/** 分页拉取该种类全部人物（单页 size 上限 200，循环取完） */
async function loadAllPersons() {
  loading.value = true
  try {
    const size = 200
    let page = 1
    const loaded: PersonBrief[] = []
    while (true) {
      const { items, total } = await api.fetchList({ page, size, sort: 'name' })
      loaded.push(...items)
      if (loaded.length >= total || items.length === 0) break
      page += 1
    }
    allPersons.value = loaded
  } catch {
    // 加载失败静默：下拉显示空态，不影响已关联列表与解除关联
  } finally {
    loading.value = false
  }
}

onMounted(loadAllPersons)

/** 选中候选人物 → 建立关联 */
async function addPerson(person: PersonBrief) {
  try {
    const result = await api.link(props.inspirationId, [person.id])
    const added = result.added ?? []
    emit('change', [...props.persons, ...added])
    Message.success(`已关联「${person.name}」`)
    selectedId.value = undefined
    searchText.value = ''
  } catch (e) {
    Message.error(getApiErrorMessage(e, '关联失败'))
  }
}

/** 下拉选择变化：命中候选则建立关联（选值清空，可重复选择同一人） */
function onSelectChange(value: unknown) {
  const person = allPersons.value.find((p) => String(p.id) === String(value))
  if (person) void addPerson(person)
}

/** 解除关联 */
async function removePerson(person: PersonBrief) {
  try {
    await api.unlink(props.inspirationId, person.id)
    emit(
      'change',
      props.persons.filter((p) => p.id !== person.id),
    )
    Message.success(`已解除「${person.name}」`)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '解除关联失败'))
  }
}
</script>

<template>
  <div class="person-link-section">
    <h4>{{ kindLabel }}</h4>

    <!-- 已关联人物 -->
    <div v-if="persons.length > 0" class="linked-list">
      <span v-for="p in persons" :key="p.id" class="linked-chip">
        <span class="linked-name">{{ p.name }}</span>
        <span class="linked-remove" title="解除关联" @click="removePerson(p)">×</span>
      </span>
    </div>
    <a-typography-text v-else type="secondary" style="font-size: 13px"
      >尚未关联{{ kindLabel }}</a-typography-text
    >

    <!-- 从已有列表选择添加 -->
    <a-select
      v-model="selectedId"
      :options="selectOptions"
      :loading="loading"
      :placeholder="`选择要关联的${kindLabel}`"
      size="small"
      allow-search
      allow-clear
      :input-value="searchText"
      :filter-option="filterOption"
      class="person-select"
      @update:input-value="(v: string) => (searchText = v)"
      @change="onSelectChange"
    >
      <template #empty>
        {{ selectOptions.length === 0 ? `暂无可选${kindLabel}` : `无匹配的${kindLabel}` }}
      </template>
    </a-select>
  </div>
</template>

<style scoped>
.person-link-section {
  margin-bottom: 20px;
}

.person-link-section h4 {
  margin: 0 0 10px;
  font-size: 16px;
}

.linked-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.linked-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px 3px 6px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #f9fafb;
}

.linked-name {
  font-size: 13px;
  color: #374151;
}

.linked-remove {
  cursor: pointer;
  color: #9ca3af;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
}

.linked-remove:hover {
  color: #ef4444;
}

.person-select {
  margin-top: 10px;
  max-width: 320px;
}
</style>
