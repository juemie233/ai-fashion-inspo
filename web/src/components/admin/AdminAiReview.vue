<script setup lang="ts">
/** 疑似 AI 素材管理：勾选素材后批量删除，或重新标记为非 AI。 */

import { ref, computed, watch, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { fetchInspirations, batchUnmarkAi, type InspirationOut } from '@/api/inspirations'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'

const message = useMessage()

const props = defineProps<{
  /** 刷新键：父组件批量删除完成后自增，触发本组件重新加载列表 */
  refreshKey: number
}>()

const emit = defineEmits<{
  (e: 'deleteSelected', ids: string[]): void
}>()

// ── 列表与分页 ──

const items = ref<InspirationOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function load() {
  loading.value = true
  try {
    const data = await fetchInspirations({
      is_ai_generated: true,
      page: page.value,
      size: pageSize,
      sort: 'newest',
    })
    items.value = data.items
    total.value = data.total
    // 清理已不在当前页的选中项，避免残留不可见选择
    const visible = new Set(data.items.map((i) => i.id))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => visible.has(id)))
  } catch {
    message.error('加载疑似 AI 素材失败')
  } finally {
    loading.value = false
  }
}

// 父组件批量删除完成后刷新本页
watch(() => props.refreshKey, () => {
  page.value = 1
  load()
})

onMounted(load)

// ── 选择状态 ──

const selectedIds = ref<Set<string>>(new Set())
const selectedCount = computed(() => selectedIds.value.size)

function toggleSelect(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function selectAllPage() {
  const next = new Set(selectedIds.value)
  items.value.forEach((i) => next.add(i.id))
  selectedIds.value = next
}

function clearSelection() {
  selectedIds.value = new Set()
}

// ── 批量操作 ──

const unmarking = ref(false)

async function unmarkSelected() {
  const ids = [...selectedIds.value]
  if (!ids.length) {
    message.warning('请先选择素材')
    return
  }
  unmarking.value = true
  try {
    const r = await batchUnmarkAi(ids)
    message.success(`已将 ${r.updated} 个素材重新标记为非 AI`)
    await load()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '标记失败')
  } finally {
    unmarking.value = false
  }
}

function deleteSelected() {
  const ids = [...selectedIds.value]
  if (!ids.length) {
    message.warning('请先选择素材')
    return
  }
  emit('deleteSelected', ids)
}
</script>

<template>
  <div class="ai-review">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <n-tag type="warning" size="small" :bordered="false">
          共 {{ total }} 个疑似 AI 素材
        </n-tag>
        <span class="view-hint">点击卡片勾选，悬停后点 👁 浏览详情</span>
        <span v-if="selectedCount > 0" class="selected-info">已选 {{ selectedCount }} 项</span>
      </div>
      <div class="toolbar-right">
        <n-button size="small" :disabled="items.length === 0" @click="selectAllPage">
          全选本页
        </n-button>
        <n-button size="small" :disabled="selectedCount === 0" @click="clearSelection">
          清空选择
        </n-button>
        <n-button
          size="small"
          type="primary"
          secondary
          :loading="unmarking"
          :disabled="selectedCount === 0"
          @click="unmarkSelected"
        >
          标记为非 AI
        </n-button>
        <n-popconfirm @positive-click="deleteSelected">
          <template #trigger>
            <n-button
              size="small"
              type="error"
              secondary
              :disabled="selectedCount === 0"
            >
              删除选中
            </n-button>
          </template>
          确定删除选中的 {{ selectedCount }} 个疑似 AI 素材？此操作物理删除文件，不可撤销。
        </n-popconfirm>
      </div>
    </div>

    <!-- 素材网格（选择模式） -->
    <MasonryGrid
      :items="items"
      :loading="loading"
      :selectable="true"
      :selected-ids="selectedIds"
      :show-actions="false"
      :hover-zoom="true"
      :show-view-button="true"
      empty-text="🎉 没有疑似 AI 的素材"
      @toggle-select="toggleSelect"
    />

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <n-pagination
        v-model:page="page"
        :page-count="totalPages"
        :page-size="pageSize"
        @update:page="load"
      />
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.view-hint {
  font-size: 12px;
  color: #999;
}
.selected-info {
  font-size: 13px;
  color: #d03050;
  font-weight: 600;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
