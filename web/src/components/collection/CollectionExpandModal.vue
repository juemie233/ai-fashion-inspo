<script setup lang="ts">
/** 合集「按此扩充」弹窗：按已有合集内容生成相似候选 → 人工勾选 → 按批次写入。
 *
 * 口径（用户已确认）：
 * - 只服务用户自建手动合集；抖音同步/智能合集入口不显示、接口也会 400；
 * - 一次性候选 + 人工确认，**打开弹窗不自动写库**；
 * - 写入走「扩充批次」，成功后可一键撤销。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import {
  expandCollection,
  suggestCollectionItems,
  type CollectionOut,
  type CollectionSuggestResult,
} from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  visible: boolean
  /** 目标合集（必须是手动合集；为 null 时不生成） */
  collection: CollectionOut | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  /** 扩充成功：父级据此显示「撤销」提示条 */
  (e: 'applied', payload: { batch_id: string; added: number }): void
}>()

const loading = ref(false)
const error = ref<string | null>(null)
const result = ref<CollectionSuggestResult | null>(null)
const selectedIds = ref<Set<string>>(new Set<string>())
const submitting = ref(false)
/** 请求序号：切换合集/重复生成时丢弃过期响应 */
let requestSeq = 0

const minSimilarity = ref(0.5)
const outfitOnly = ref(false)
const excludeOtherAuto = ref(true)

const items = computed(() => result.value?.items.map((item) => item.inspiration) ?? [])
/** 卡片角标：相似度 + 共同标签数（复用 MasonryGrid 的 badges） */
const badges = computed(() => {
  const map: Record<string, string> = {}
  for (const item of result.value?.items ?? []) {
    const percent = Math.round(item.similarity * 100)
    const tagPart = item.shared_tags.length > 0 ? ` · ${item.shared_tags.length} 标签` : ''
    map[item.inspiration.id] = `${percent}%${tagPart}`
  }
  return map
})
const selectedCount = computed(() => selectedIds.value.size)
const allSelected = computed(
  () => items.value.length > 0 && items.value.every((item) => selectedIds.value.has(item.id)),
)
const profileText = computed(() => {
  const profile = result.value?.profile
  if (!profile) return ''
  const mode = profile.vector_available ? '视觉 + 标签' : '仅标签（向量不可用）'
  const outfit =
    profile.outfit_tags.length > 0 ? ` · 大标签：${profile.outfit_tags.join('、')}` : ''
  return `种子 ${profile.seeds_used} 条 · 匹配方式：${mode}${outfit}`
})

async function generate(): Promise<void> {
  const collection = props.collection
  if (!collection) return
  const seq = ++requestSeq
  loading.value = true
  error.value = null
  result.value = null
  selectedIds.value = new Set<string>()
  try {
    const data = await suggestCollectionItems(collection.id, {
      limit: 40,
      min_similarity: minSimilarity.value,
      outfit_only: outfitOnly.value,
      exclude_other_auto: excludeOtherAuto.value,
    })
    if (seq !== requestSeq) return
    result.value = data
  } catch (e) {
    if (seq !== requestSeq) return
    error.value = getApiErrorMessage(e, '生成候选失败')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function toggleSelect(id: string): void {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll(): void {
  selectedIds.value = allSelected.value
    ? new Set<string>()
    : new Set(items.value.map((item) => item.id))
}

function clearSelection(): void {
  selectedIds.value = new Set<string>()
}

async function submit(): Promise<void> {
  const collection = props.collection
  if (!collection || selectedCount.value === 0 || submitting.value) return
  submitting.value = true
  try {
    const data = await expandCollection(collection.id, [...selectedIds.value])
    Message.success(
      data.skipped > 0
        ? `已扩充 ${data.added} 个素材（${data.skipped} 个已在合集内）`
        : `已扩充 ${data.added} 个素材`,
    )
    emit('applied', { batch_id: data.batch_id, added: data.added })
    emit('update:visible', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '扩充失败'))
  } finally {
    submitting.value = false
  }
}

// 打开或切换合集：作废旧请求并重新生成（筛选条件回到默认）
watch(
  [() => props.visible, () => props.collection?.id],
  ([visible]) => {
    requestSeq++
    if (!visible) {
      loading.value = false
      result.value = null
      selectedIds.value = new Set<string>()
      return
    }
    minSimilarity.value = 0.5
    outfitOnly.value = false
    excludeOtherAuto.value = true
    void generate()
  },
  { immediate: true },
)
</script>

<template>
  <a-modal
    :visible="visible"
    :width="'min(1080px, calc(100vw - 32px))'"
    title="按此合集扩充"
    :footer="false"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="expand-hint">
      从素材库里找出与此合集内容相近、但还没加入的素材。勾选后才写入（可一键撤销）。
    </p>

    <div class="expand-toolbar">
      <div class="expand-control">
        <span class="expand-label">相似度 ≥ {{ Math.round(minSimilarity * 100) }}%</span>
        <a-slider
          v-model="minSimilarity"
          :min="0"
          :max="0.9"
          :step="0.05"
          style="width: 180px"
          @change="() => void generate()"
        />
      </div>
      <a-checkbox v-model="outfitOnly" @change="() => void generate()">
        只用大标签相近的素材
      </a-checkbox>
      <a-checkbox v-model="excludeOtherAuto" @change="() => void generate()">
        排除其它抖音收藏夹里的素材
      </a-checkbox>
      <a-button size="small" :loading="loading" @click="() => void generate()">重新生成</a-button>
    </div>

    <p v-if="profileText" class="expand-profile">{{ profileText }}</p>

    <div v-if="error" class="expand-error">
      <span class="expand-error-text">{{ error }}</span>
      <a-button size="mini" @click="() => void generate()">重试</a-button>
    </div>

    <div v-if="loading" class="expand-loading">
      <a-spin />
      <span>正在按合集内容匹配素材…</span>
    </div>

    <template v-else-if="result">
      <div v-if="items.length === 0" class="expand-empty">
        没有找到合适的候选。可以降低相似度、关闭「只用大标签相近的素材」，或先在合集里多放几张种子素材。
      </div>
      <template v-else>
        <div class="expand-select-bar">
          <a-button size="mini" @click="toggleSelectAll">
            {{ allSelected ? '取消全选' : '全选本页' }}
          </a-button>
          <a-button size="mini" type="text" :disabled="selectedCount === 0" @click="clearSelection">
            清除选择
          </a-button>
          <span class="expand-count"
            >共 {{ items.length }} 个候选 · 已选 {{ selectedCount }} 个</span
          >
        </div>
        <div class="expand-grid">
          <MasonryGrid
            :items="items"
            :badges="badges"
            density="compact"
            selectable
            :selected-ids="selectedIds"
            :show-actions="false"
            @toggle-select="toggleSelect"
          />
        </div>
      </template>
    </template>

    <div class="expand-footer">
      <a-button @click="emit('update:visible', false)">取消</a-button>
      <a-button
        type="primary"
        :loading="submitting"
        :disabled="selectedCount === 0"
        @click="() => void submit()"
      >
        加入合集{{ selectedCount > 0 ? `（${selectedCount}）` : '' }}
      </a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.expand-hint {
  margin: 0 0 12px;
  color: var(--color-text-3);
  font-size: 12px;
}

.expand-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
}

.expand-control {
  display: flex;
  align-items: center;
  gap: 8px;
}

.expand-label {
  font-size: 12px;
  color: var(--color-text-2);
  white-space: nowrap;
}

.expand-profile {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-3);
}

.expand-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.expand-error-text {
  flex: 1;
  color: rgb(var(--danger-6));
  font-size: 13px;
}

.expand-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 160px;
  color: var(--color-text-3);
}

.expand-empty {
  padding: 32px 12px;
  text-align: center;
  color: var(--color-text-3);
}

.expand-select-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.expand-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-3);
}

.expand-grid {
  max-height: calc(100dvh - 360px);
  overflow-y: auto;
  overscroll-behavior: contain;
}

.expand-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-2);
}
</style>
