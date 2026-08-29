<script setup lang="ts">
/** 合集选择器弹窗：把素材加入某个手动合集；支持现场新建。
 *
 * 被「素材库批量操作栏」与「素材详情页」共用。智能合集不可选（内容由条件
 * 动态决定，设计契约禁止手动加入）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  addToCollection,
  createCollection,
  fetchCollections,
  type CollectionOut,
} from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  visible: boolean
  /** 待加入的素材 ID 列表 */
  inspirationIds: string[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  /** 加入成功（含新建后直接加入）后触发，供调用方刷新列表 */
  (e: 'added', collectionId: number, addedCount: number): void
}>()

const collections = ref<CollectionOut[]>([])
const loading = ref(false)
const submitting = ref(false)
const selectedId = ref<number | null>(null)
const creating = ref(false)
const newName = ref('')
const newDesc = ref('')

const manualCollections = computed(() => collections.value.filter((c) => c.kind === 'manual'))

watch(
  () => props.visible,
  async (v) => {
    if (!v) return
    selectedId.value = null
    creating.value = false
    newName.value = ''
    newDesc.value = ''
    loading.value = true
    try {
      collections.value = await fetchCollections()
    } catch {
      Message.error('加载合集列表失败')
    } finally {
      loading.value = false
    }
  },
)

async function confirmCreateAndAdd() {
  const name = newName.value.trim()
  if (!name) {
    Message.warning('请输入合集名称')
    return
  }
  submitting.value = true
  try {
    const created = await createCollection({ name, description: newDesc.value.trim() || null })
    Message.success(`已创建合集「${name}」`)
    const added = await addIn(created.id)
    if (added) emit('added', created.id, added)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建合集失败'))
  } finally {
    submitting.value = false
  }
}

/** 加入已选合集，返回加入数量（失败返回 0） */
async function addIn(collectionId: number): Promise<number | null> {
  submitting.value = true
  try {
    const { added } = await addToCollection(collectionId, props.inspirationIds)
    Message.success(`已加入 ${added} 个素材`)
    emit('update:visible', false)
    return added
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加入合集失败'))
    return null
  } finally {
    submitting.value = false
  }
}

function confirmAdd() {
  if (selectedId.value === null) {
    Message.warning('请选择一个合集')
    return
  }
  void addIn(selectedId.value)
}
</script>

<template>
  <a-modal
    :visible="visible"
    title="加入合集"
    style="width: 480px"
    @update:visible="emit('update:visible', $event)"
  >
    <a-spin :loading="loading" style="display: block">
      <div v-if="manualCollections.length > 0 && !creating" class="collection-options">
        <div
          v-for="c in manualCollections"
          :key="c.id"
          class="collection-option"
          :class="{ selected: selectedId === c.id }"
          @click="selectedId = c.id"
        >
          <span class="collection-icon">📁</span>
          <span class="collection-name">{{ c.name }}</span>
          <span class="collection-count">{{ c.item_count ?? 0 }} 个素材</span>
        </div>
      </div>
      <a-empty v-else-if="!creating" description="还没有手动合集" style="padding: 12px 0" />

      <a-button v-if="!creating" size="small" long @click="creating = true">＋ 新建合集</a-button>

      <div v-if="creating" class="create-form">
        <a-input v-model="newName" placeholder="合集名称（1~50 字）" allow-clear max-length="50" />
        <a-input v-model="newDesc" placeholder="描述（可选）" allow-clear />
        <div style="display: flex; gap: 8px; justify-content: flex-end">
          <a-button size="small" @click="creating = false">返回选择</a-button>
        </div>
      </div>
    </a-spin>

    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button size="small" @click="emit('update:visible', false)">取消</a-button>
        <a-button
          v-if="creating"
          size="small"
          type="primary"
          :loading="submitting"
          @click="confirmCreateAndAdd"
        >
          创建并加入
        </a-button>
        <a-button
          v-else
          size="small"
          type="primary"
          :loading="submitting"
          :disabled="selectedId === null"
          @click="confirmAdd"
        >
          加入（{{ inspirationIds.length }} 个素材）
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.collection-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  max-height: 280px;
  overflow-y: auto;
}
.collection-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  cursor: pointer;
}
.collection-option:hover {
  background: #f5f7fa;
}
.collection-option.selected {
  border-color: #2080f0;
  background: #eef4ff;
}
.collection-icon {
  font-size: 14px;
}
.collection-name {
  flex: 1;
  font-size: 13px;
}
.collection-count {
  font-size: 12px;
  color: #86909c;
}
.create-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
}
</style>
