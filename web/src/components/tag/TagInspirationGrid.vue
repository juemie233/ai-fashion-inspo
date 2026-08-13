<script setup lang="ts">
/** 标签素材网格：展示某标签关联的素材，支持跳转详情、悬停快捷操作、多选批量移除。 */

import { ref, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import {
  fetchTagInspirations,
  batchRemoveTagInspirations,
  type TagInspiration,
  type TagItem,
} from '@/api/tags'
import { getFileUrl, removeTagFromInspiration } from '@/api/inspirations'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'

const props = defineProps<{
  /** 当前选中的标签 */
  tag: TagItem | null
}>()

const emit = defineEmits<{
  /** 素材关联数发生变化（单个移除=1，批量移除=N），供父组件同步 usage_count/统计 */
  (e: 'changed', payload: { removed: number }): void
}>()

const router = useRouter()
const message = useMessage()

// ===== 列表数据 =====
const items = ref<TagInspiration[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const sort = ref<'newest' | 'oldest' | 'confidence'>('newest')
const density = ref<'compact' | 'standard'>('compact')

// ===== 多选（批量移除） =====
const selectedIds = ref<Set<string>>(new Set())
const batchRemoving = ref(false)

// ===== 单个移除中 =====
const removingIds = ref<Set<string>>(new Set())

// ===== 灯箱 =====
const showLightbox = ref(false)
const lightboxPath = ref('')

const selectedCount = computed(() => selectedIds.value.size)
const allVisibleSelected = computed(
  () => items.value.length > 0 && items.value.every((i) => selectedIds.value.has(i.inspiration_id)),
)

/** 切换标签时重置状态并重新加载 */
watch(
  () => props.tag?.id,
  () => {
    page.value = 1
    selectedIds.value = new Set()
    load(true)
  },
  { immediate: true },
)

async function load(reset = true) {
  if (!props.tag) return
  if (reset) page.value = 1
  loading.value = true
  try {
    const data = await fetchTagInspirations(props.tag.id, page.value, 50, sort.value)
    items.value = reset ? data.items : [...items.value, ...data.items]
    total.value = data.total
  } catch {
    message.error('加载素材失败')
  } finally {
    loading.value = false
  }
}

function onSortChange() {
  load(true)
}

function loadMore() {
  page.value += 1
  load(false)
}

/** A 方案：单击缩略图跳转详情页 */
function openDetail(item: TagInspiration) {
  router.push({ name: 'detail', params: { id: item.inspiration_id } })
}

/** C 方案：悬停快捷操作——移除该标签 */
async function removeOne(item: TagInspiration) {
  if (!props.tag || removingIds.value.has(item.inspiration_id)) return
  removingIds.value = new Set(removingIds.value).add(item.inspiration_id)
  try {
    await removeTagFromInspiration(item.inspiration_id, props.tag.id)
    items.value = items.value.filter((i) => i.inspiration_id !== item.inspiration_id)
    total.value = Math.max(0, total.value - 1)
    selectedIds.value.delete(item.inspiration_id)
    selectedIds.value = new Set(selectedIds.value)
    emit('changed', { removed: 1 })
    message.success('已移除该标签')
  } catch {
    message.error('移除失败')
  } finally {
    const next = new Set(removingIds.value)
    next.delete(item.inspiration_id)
    removingIds.value = next
  }
}

/** C 方案：悬停快捷操作——看大图 */
function openLightbox(item: TagInspiration) {
  lightboxPath.value = item.file_path
  showLightbox.value = true
}

// ===== F 方案：多选批量移除 =====
function toggleSelect(id: string) {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
  selectedIds.value = new Set(selectedIds.value)
}

function toggleSelectAll() {
  selectedIds.value = allVisibleSelected.value
    ? new Set()
    : new Set(items.value.map((i) => i.inspiration_id))
}

function clearSelection() {
  selectedIds.value = new Set()
}

async function batchRemove() {
  if (!props.tag || selectedCount.value === 0) return
  const ids = [...selectedIds.value]
  batchRemoving.value = true
  try {
    const { removed } = await batchRemoveTagInspirations(props.tag.id, ids)
    message.success(`已从 ${removed} 个素材移除该标签`)
    selectedIds.value = new Set()
    emit('changed', { removed })
    await load(true)
  } catch {
    message.error('批量移除失败')
  } finally {
    batchRemoving.value = false
  }
}

/** 素材缩略图 URL（无缩略图时回退到原图） */
function fileUrl(item: TagInspiration): string {
  return getFileUrl(item.thumbnail_path || item.file_path)
}
</script>

<template>
  <template v-if="tag">
    <!-- 头部：标签名 + 排序 + 密度 -->
    <div class="grid-header">
      <n-space align="center" :wrap="false">
        <h3 style="margin:0">「{{ tag.name }}」</h3>
        <n-tag size="small" :bordered="false">{{ tag.usage_count }} 次</n-tag>
        <span style="font-size:13px;color:#999">共 {{ total }} 个</span>
      </n-space>
      <n-space size="small">
        <n-select
          :value="sort"
          :options="[
            { label: '最新', value: 'newest' },
            { label: '最旧', value: 'oldest' },
            { label: '置信度', value: 'confidence' },
          ]"
          size="tiny"
          style="width:90px"
          @update:value="sort = $event; onSortChange()"
        />
        <n-button-group size="tiny">
          <n-button :type="density === 'compact' ? 'primary' : 'default'" @click="density = 'compact'">⊞</n-button>
          <n-button :type="density === 'standard' ? 'primary' : 'default'" @click="density = 'standard'">⊟</n-button>
        </n-button-group>
      </n-space>
    </div>

    <!-- 批量操作栏（选中后出现） -->
    <div v-if="selectedCount > 0" class="batch-bar">
      <n-space align="center" :size="8">
        <n-checkbox :checked="allVisibleSelected" :indeterminate="selectedCount > 0 && !allVisibleSelected" @update:checked="toggleSelectAll" />
        <span style="font-size:13px">已选 {{ selectedCount }} 个</span>
        <n-button size="tiny" type="error" :loading="batchRemoving" @click="batchRemove">
          批量移除该标签
        </n-button>
        <n-button size="tiny" @click="clearSelection">取消选择</n-button>
      </n-space>
    </div>

    <n-spin :show="loading">
      <div v-if="items.length === 0 && !loading" class="grid-empty">暂无素材</div>
      <div v-else :class="['image-grid', 'density-' + density]">
        <div
          v-for="item in items"
          :key="item.inspiration_id"
          class="image-card"
          :class="{ 'is-selected': selectedIds.has(item.inspiration_id) }"
          :title="`置信度: ${(item.confidence * 100).toFixed(0)}%`"
          @click="openDetail(item)"
        >
          <img v-if="item.thumbnail_path || item.file_path" :src="fileUrl(item)" :alt="tag.name" loading="lazy" />
          <div v-else class="no-preview">无预览</div>

          <!-- 多选勾选 -->
          <n-checkbox
            class="card-checkbox"
            :checked="selectedIds.has(item.inspiration_id)"
            @update:checked="toggleSelect(item.inspiration_id)"
            @click.stop
          />

          <!-- 悬停快捷操作 -->
          <div class="card-actions" @click.stop>
            <n-button
              size="tiny"
              type="error"
              ghost
              :loading="removingIds.has(item.inspiration_id)"
              @click="removeOne(item)"
            >移除</n-button>
            <n-button size="tiny" @click="openLightbox(item)">大图</n-button>
          </div>

          <!-- 选中遮罩 -->
          <div v-if="selectedIds.has(item.inspiration_id)" class="card-selected-mask" />
        </div>
      </div>

      <div v-if="items.length < total" style="text-align:center;padding:12px">
        <n-button size="small" :loading="loading" @click="loadMore">加载更多（{{ items.length }}/{{ total }}）</n-button>
      </div>
    </n-spin>

    <!-- 灯箱 -->
    <ImageLightbox :show="showLightbox" :image-path="lightboxPath" @close="showLightbox = false" />
  </template>

  <div v-else class="grid-placeholder">点击左侧标签查看关联素材</div>
</template>

<style scoped>
.grid-header {
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.grid-placeholder,
.grid-empty {
  color: #999;
  text-align: center;
  padding: 60px 20px;
  font-size: 14px;
}

.batch-bar {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  margin-bottom: 10px;
  background: #f0f6ff;
  border: 1px solid #c8dfff;
  border-radius: 6px;
}

.image-grid {
  display: grid;
}
.image-grid.density-compact {
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.image-grid.density-standard {
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.image-card {
  position: relative;
  cursor: pointer;
  border-radius: 4px;
  overflow: hidden;
  transition: transform 0.15s;
}
.image-card:hover {
  transform: scale(1.03);
}
.image-card img {
  width: 100%;
  aspect-ratio: 2/3;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}

.no-preview {
  width: 100%;
  aspect-ratio: 2/3;
  background: #f5f5f5;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ccc;
  font-size: 12px;
}

/* 多选勾选：悬停或已选中时显示 */
.card-checkbox {
  position: absolute;
  top: 4px;
  left: 4px;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  padding: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}
.image-card:hover .card-checkbox,
.image-card.is-selected .card-checkbox {
  opacity: 1;
}

/* 悬停快捷操作 */
.card-actions {
  position: absolute;
  bottom: 4px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  gap: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}
.image-card:hover .card-actions {
  opacity: 1;
}

/* 选中遮罩 */
.card-selected-mask {
  position: absolute;
  inset: 0;
  border: 2px solid #3b82f6;
  background: rgba(59, 130, 246, 0.15);
  border-radius: 4px;
  pointer-events: none;
}

@media (max-width: 900px) {
  .image-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
