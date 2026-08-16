<script setup lang="ts">
/** 素材库批量选择操作栏：收藏 / 移垃圾桶 / 加标签 / 编辑元数据 / 全选 / 退出。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import type { BatchUpdateFields } from '@/api/inspirations'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'

defineProps<{
  /** 已勾选数量 */
  count: number
  /** 当前页是否全选 */
  allSelected: boolean
  /** 已有标签名（加标签时的候选，可按需新建） */
  tagOptions: string[]
}>()

const emit = defineEmits<{
  (e: 'favorite', isFavorite: boolean): void
  (e: 'trash'): void
  (e: 'select-all'): void
  (e: 'add-tags', names: string[]): void
  (e: 'update', fields: BatchUpdateFields): void
  (e: 'exit'): void
}>()

const message = useMessage()

const sourceOptions = Object.entries(SOURCE_TYPE_LABELS).map(([value, label]) => ({
  label,
  value,
}))
const qualityOptions = [
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]
const aiOptions = [
  { label: '是', value: true },
  { label: '否', value: false },
]

// ── 加标签弹窗 ──
const tagModalOpen = ref(false)
const tagInput = ref<string[]>([])

function openTagModal() {
  tagInput.value = []
  tagModalOpen.value = true
}

function confirmTags() {
  if (tagInput.value.length === 0) {
    message.warning('请输入至少一个标签')
    return
  }
  emit('add-tags', tagInput.value)
  tagModalOpen.value = false
}

// ── 编辑元数据弹窗 ──
const editModalOpen = ref(false)
const editSource = ref<string | null>(null)
const editQuality = ref<'pending' | 'approved' | 'rejected' | null>(null)
const editAi = ref<boolean | null>(null)

function openEditModal() {
  editSource.value = null
  editQuality.value = null
  editAi.value = null
  editModalOpen.value = true
}

function confirmEdit() {
  const fields: BatchUpdateFields = {}
  if (editSource.value) fields.source_type = editSource.value
  if (editQuality.value) fields.quality_status = editQuality.value
  if (editAi.value !== null) fields.is_ai_generated = editAi.value
  if (Object.keys(fields).length === 0) {
    message.warning('请选择至少一个要修改的字段')
    return
  }
  emit('update', fields)
  editModalOpen.value = false
}
</script>

<template>
  <div class="batch-bar">
    <span class="batch-count">已选 {{ count }} 个</span>
    <n-button size="tiny" @click="emit('favorite', true)">批量收藏</n-button>
    <n-button size="tiny" @click="emit('favorite', false)">取消收藏</n-button>
    <n-button size="tiny" @click="openTagModal">加标签</n-button>
    <n-button size="tiny" @click="openEditModal">编辑元数据</n-button>
    <n-popconfirm @positive-click="emit('trash')">
      <template #trigger>
        <n-button size="tiny" type="error" secondary>移入垃圾桶</n-button>
      </template>
      将所选 {{ count }} 个素材移入垃圾桶？保留期内可恢复
    </n-popconfirm>
    <n-button size="tiny" @click="emit('select-all')">
      {{ allSelected ? '取消全选' : '全选本页' }}
    </n-button>
    <div style="flex: 1" />
    <n-button size="tiny" quaternary @click="emit('exit')">退出批量</n-button>
  </div>

  <!-- 加标签弹窗 -->
  <n-modal v-model:show="tagModalOpen" preset="card" title="批量添加标签" style="width: 460px">
    <p style="color: #999; font-size: 12px">为所选 {{ count }} 个素材批量关联标签（已关联的自动跳过）。</p>
    <n-select
      v-model:value="tagInput"
      multiple
      filterable
      tag
      clearable
      placeholder="输入标签名，回车新建；也可从已有标签中选择"
      :options="tagOptions.map((name) => ({ label: name, value: name }))"
    />
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <n-button size="small" @click="tagModalOpen = false">取消</n-button>
        <n-button size="small" type="primary" @click="confirmTags">确定</n-button>
      </div>
    </template>
  </n-modal>

  <!-- 编辑元数据弹窗 -->
  <n-modal v-model:show="editModalOpen" preset="card" title="批量编辑元数据" style="width: 460px">
    <p style="color: #999; font-size: 12px">仅更新所选 {{ count }} 个素材中你显式填写的字段，其余保持不变。</p>
    <n-form label-placement="left" label-width="80" size="small">
      <n-form-item label="来源">
        <n-select v-model:value="editSource" clearable placeholder="不修改" :options="sourceOptions" />
      </n-form-item>
      <n-form-item label="审核状态">
        <n-select v-model:value="editQuality" clearable placeholder="不修改" :options="qualityOptions" />
      </n-form-item>
      <n-form-item label="疑似 AI">
        <n-select v-model:value="editAi" clearable placeholder="不修改" :options="aiOptions" />
      </n-form-item>
    </n-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <n-button size="small" @click="editModalOpen = false">取消</n-button>
        <n-button size="small" type="primary" @click="confirmEdit">确定</n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #eef4ff;
  border: 1px solid #c8d8f5;
  border-radius: 8px;
}
.batch-count {
  font-size: 13px;
  color: #2d5b9a;
  font-weight: 500;
}
</style>
