<script setup lang="ts">
/** 合集目标选择器（公共）：把素材移动/复制到目标合集，或把当前合集合并到目标合集。
 *
 * 与「加入合集」的 CollectionPickerModal 共用同一套可选范围规则：
 * 只列**手动合集**里能装素材的**容器节点**——智能合集（内容由条件动态决定）、
 * 分类节点（一级同步节点或已有子收藏夹）与自动合集（auto_source 非空）都不可选。
 * 目标支持多选（最多 20 个）用于批量移动/复制；合并模式由调用方传 multiple=false。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { fetchCollections, type CollectionOut } from '@/api/collections'

const props = withDefaults(
  defineProps<{
    visible: boolean
    title?: string
    /** 是否多选目标（批量移动/复制=true；合并=false） */
    multiple?: boolean
    /** 排除的合集 id（源合集自身/当前合集） */
    excludeIds?: number[]
    /** 最多选择几个目标 */
    max?: number
    /** 父组件提交中：禁用确认防连点 */
    submitting?: boolean
  }>(),
  {
    title: '选择目标合集',
    multiple: true,
    excludeIds: () => [],
    max: 20,
    submitting: false,
  },
)

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  /** 确认选择：返回目标合集 id（合并模式只有一个） */
  (e: 'confirm', targetIds: number[]): void
}>()

const collections = ref<CollectionOut[]>([])
const loading = ref(false)
const selectedIds = ref<number[]>([])

/** 可选目标：手动合集 + 容器节点（规则与 CollectionPickerModal.manualCollections 一致）。
 *
 * 必须排除**任何** auto_source（含抖音二级收藏夹）：后端 _require_transferable 对
 * auto_source 非空一律 400，只排除一级会让弹窗给出必然失败的目标。 */
const candidates = computed(() =>
  collections.value.filter(
    (c) =>
      c.kind === 'manual' && !c.auto_source && !c.child_count && !props.excludeIds.includes(c.id),
  ),
)

watch(
  () => props.visible,
  async (visible) => {
    if (!visible) return
    selectedIds.value = []
    loading.value = true
    try {
      collections.value = await fetchCollections()
    } catch {
      Message.error('加载合集列表失败')
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function toggle(id: number): void {
  if (props.multiple) {
    if (selectedIds.value.includes(id)) {
      selectedIds.value = selectedIds.value.filter((v) => v !== id)
      return
    }
    if (selectedIds.value.length >= props.max) {
      Message.warning(`最多选择 ${props.max} 个目标合集`)
      return
    }
    selectedIds.value = [...selectedIds.value, id]
    return
  }
  selectedIds.value = [id]
}

function confirm(): void {
  if (selectedIds.value.length === 0) {
    Message.warning('请选择目标合集')
    return
  }
  emit('confirm', [...selectedIds.value])
}
</script>

<template>
  <a-modal
    :visible="visible"
    :width="480"
    :title="title"
    @update:visible="emit('update:visible', $event)"
  >
    <a-spin :loading="loading" style="display: block">
      <div v-if="candidates.length > 0" class="target-options">
        <div
          v-for="c in candidates"
          :key="c.id"
          class="target-option"
          :class="{ selected: selectedIds.includes(c.id) }"
          role="checkbox"
          :aria-checked="selectedIds.includes(c.id)"
          tabindex="0"
          @click="toggle(c.id)"
          @keydown.enter="toggle(c.id)"
          @keydown.space.prevent="toggle(c.id)"
        >
          <span class="target-check" aria-hidden="true">
            {{ selectedIds.includes(c.id) ? '✓' : '' }}
          </span>
          <span class="target-name">{{ c.name }}</span>
          <span class="target-count">{{ c.item_count ?? 0 }} 个素材</span>
        </div>
      </div>
      <a-empty v-else description="没有可选的手动合集" style="padding: 12px 0" />
    </a-spin>

    <template #footer>
      <div class="target-footer">
        <span v-if="multiple" class="target-hint">已选 {{ selectedIds.length }} / {{ max }}</span>
        <a-button size="small" @click="emit('update:visible', false)">取消</a-button>
        <a-button
          size="small"
          type="primary"
          :loading="submitting"
          :disabled="selectedIds.length === 0"
          @click="confirm"
        >
          确定
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.target-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  overflow-y: auto;
}

.target-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  cursor: pointer;
  background: var(--sheet);
}

.target-option:hover {
  background: var(--sheet-2);
}

.target-option.selected {
  border-color: var(--pine);
  background: var(--paper);
}

.target-check {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  line-height: 1;
  color: var(--pine);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--sheet);
}

.target-option.selected .target-check {
  border-color: var(--pine);
}

.target-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}

.target-count {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--ink-3);
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
}

.target-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.target-hint {
  margin-right: auto;
  font-size: 12px;
  color: var(--ink-3);
}
</style>
