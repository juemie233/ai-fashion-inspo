<script setup lang="ts">
/** 素材详情「关联人物」区块：展示已关联人物，支持搜索添加与解除关联。
 *
 * 关联一律使用 person_id（不按名称匹配），规避同名多人歧义。
 */

import { onBeforeUnmount, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { linkPerson, unlinkPerson, suggestPersons } from '@/api/persons'
import type { PersonBrief } from '@shared/types/person'
import { PERSON_TYPE_LABELS } from '@shared/types/person'
import PersonTypeTag from './PersonTypeTag.vue'

const props = defineProps<{
  /** 当前素材已关联的人物 */
  persons: PersonBrief[]
  /** 素材 ID */
  inspirationId: string
}>()

const emit = defineEmits<{
  (e: 'change', persons: PersonBrief[]): void
}>()

const message = useMessage()

/** 搜索输入 */
const keyword = ref('')
/** 搜索建议（去重后的候选人物） */
const suggestions = ref<PersonBrief[]>([])
/** 是否正在搜索 */
const searching = ref(false)
/** 搜索防抖定时器（避免每敲一个字就请求一次接口） */
let searchTimer: number | null = null
/** 请求序号：丢弃过期响应，防止慢的旧请求覆盖新建议 */
let searchSeq = 0

/** 按名称搜索候选人物（300ms 防抖 + 序号防乱序，排除已关联的） */
function onSearch() {
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer)
    searchTimer = null
  }
  const name = keyword.value.trim()
  if (!name) {
    suggestions.value = []
    return
  }
  searchTimer = window.setTimeout(async () => {
    const seq = ++searchSeq
    searching.value = true
    try {
      const list = await suggestPersons(name)
      if (seq !== searchSeq) return  // 已有更新的请求，丢弃过期响应
      const linkedIds = new Set(props.persons.map((p) => p.id))
      suggestions.value = list.filter((p) => !linkedIds.has(p.id))
    } catch {
      if (seq === searchSeq) suggestions.value = []
    } finally {
      if (seq === searchSeq) searching.value = false
    }
  }, 300)
}

onBeforeUnmount(() => {
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer)
  }
})

/** 选中候选人物 → 建立关联 */
async function addPerson(person: PersonBrief) {
  try {
    const result = await linkPerson(props.inspirationId, [person.id])
    const added = result.added ?? []
    const merged = [...props.persons, ...added]
    emit('change', merged)
    message.success(`已关联「${person.name}」`)
    keyword.value = ''
    suggestions.value = []
  } catch (e: any) {
    message.error(e.response?.data?.detail || '关联失败')
  }
}

/** 解除关联 */
async function removePerson(person: PersonBrief) {
  try {
    await unlinkPerson(props.inspirationId, person.id)
    emit('change', props.persons.filter((p) => p.id !== person.id))
    message.success(`已解除「${person.name}」`)
  } catch (e: any) {
    message.error(e.response?.data?.detail || '解除关联失败')
  }
}
</script>

<template>
  <div class="person-link-section">
    <h4>人物</h4>

    <!-- 已关联人物 -->
    <div v-if="persons.length > 0" class="linked-list">
      <span
        v-for="p in persons"
        :key="p.id"
        class="linked-chip"
      >
        <PersonTypeTag :type="p.person_type" />
        <span class="linked-name">{{ p.name }}</span>
        <span class="linked-remove" title="解除关联" @click="removePerson(p)">×</span>
      </span>
    </div>
    <n-text v-else depth="3" style="font-size: 13px">尚未关联人物</n-text>

    <!-- 搜索添加 -->
    <div class="search-row">
      <n-auto-complete
        v-model:value="keyword"
        :options="suggestions.map((s) => ({ label: `${s.name}（${PERSON_TYPE_LABELS[s.person_type] || s.person_type}）`, value: String(s.id) }))"
        :loading="searching"
        placeholder="输入人物名称搜索并添加"
        size="small"
        clearable
        style="flex: 1"
        @update:value="onSearch"
        @select="(val: string | number) => { const p = suggestions.find((s) => String(s.id) === String(val)); if (p) addPerson(p) }"
      />
    </div>
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

.search-row {
  display: flex;
  align-items: center;
}
</style>
