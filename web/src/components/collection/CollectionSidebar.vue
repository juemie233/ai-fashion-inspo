<script setup lang="ts">
/** 合集列表侧栏：一级/二级两级树、折叠、拖拽排序、新建入口。
 *
 * 从 CollectionsView.vue 抽出（视图只做编排）。拖拽的「起点 ID」仍由视图侧的
 * useCollectionListDnd 持有，本组件只把选中态与悬停高亮回抛给父组件。
 */

import { computed, ref } from 'vue'
import type { CollectionOut } from '@/api/collections'

const props = defineProps<{
  /** 全部合集（一级与二级混排，后端保证「一级后面紧跟它的二级」） */
  collections: CollectionOut[]
  /** 父级 ID → 直属二级收藏夹（由视图统一算好传入，避免两处各写一遍过滤） */
  childrenByParent: Map<number, CollectionOut[]>
  /** 当前选中的合集 ID */
  currentId: number | null
  /** 列表是否加载中 */
  loading: boolean
  /** 拖拽悬停的一级合集 ID（与视图侧 useCollectionListDnd 同源） */
  dragOverId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:currentId', id: number): void
  (e: 'update:dragOverId', id: number | null): void
  (e: 'drag-start', id: number): void
  (e: 'drop', id: number): void
  /** 新建手动合集 / 新建智能合集 / 在某一级下新建二级收藏夹 */
  (e: 'create-manual'): void
  (e: 'create-smart'): void
  (e: 'create-child', parentId: number): void
}>()

/** 一级合集（parent_id 为空），二级跟在各自一级下面渲染 */
const rootCollections = computed(() => props.collections.filter((c) => c.parent_id === null))

/** 折叠的一级节点（默认展开：抖音收藏夹要一眼看得见） */
const collapsed = ref<Set<number>>(new Set())

function toggleCollapse(id: number) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

/** 列表项副标题：一级显示子夹数（+ 素材数），二级显示素材数 */
function itemMeta(c: CollectionOut): string {
  if (c.kind === 'smart') return '智能合集'
  const own = c.item_count ?? 0
  const children = props.childrenByParent.get(c.id)?.length ?? 0
  // 分类节点自己没有成员时只报收藏夹数：写「0 个素材」会被读成「这个夹是空的」
  if (children > 0) return own > 0 ? `${children} 个收藏夹 · ${own} 个素材` : `${children} 个收藏夹`
  return `${own} 个素材`
}
</script>

<template>
  <aside class="collection-list">
    <div class="list-header">
      <span>合集（{{ collections.length }}）</span>
      <div style="display: flex; gap: 4px">
        <a-button size="mini" type="primary" @click="emit('create-manual')">＋ 手动</a-button>
        <a-button size="mini" @click="emit('create-smart')">⚡ 智能</a-button>
      </div>
    </div>

    <a-spin :loading="loading" style="display: block">
      <!-- 两级：每个一级渲染成组，二级紧随其下（缩进 + 折角） -->
      <template v-for="c in rootCollections" :key="c.id">
        <div
          class="collection-item"
          :class="{ active: c.id === currentId, 'drag-over': dragOverId === c.id }"
          draggable="true"
          @dragstart="emit('drag-start', c.id)"
          @dragover.prevent="emit('update:dragOverId', c.id)"
          @dragleave="emit('update:dragOverId', null)"
          @drop.prevent="emit('drop', c.id)"
          @click="emit('update:currentId', c.id)"
        >
          <span
            v-if="c.child_count > 0"
            class="collapse-toggle"
            :title="collapsed.has(c.id) ? '展开二级收藏夹' : '收起二级收藏夹'"
            @click.stop="toggleCollapse(c.id)"
          >
            {{ collapsed.has(c.id) ? '▸' : '▾' }}
          </span>
          <span v-else class="collapse-toggle placeholder"></span>
          <span class="collection-kind">{{ c.kind === 'smart' ? '⚡' : '📁' }}</span>
          <div class="collection-info">
            <div class="collection-name" :title="c.name">{{ c.name }}</div>
            <div class="collection-meta">{{ itemMeta(c) }}</div>
          </div>
        </div>

        <template v-if="!collapsed.has(c.id)">
          <div
            v-for="child in childrenByParent.get(c.id) ?? []"
            :key="child.id"
            class="collection-item child"
            :class="{ active: child.id === currentId }"
            @click="emit('update:currentId', child.id)"
          >
            <span class="collection-kind">└</span>
            <div class="collection-info">
              <div class="collection-name" :title="child.name">{{ child.name }}</div>
              <div class="collection-meta">{{ itemMeta(child) }}</div>
            </div>
          </div>
          <div v-if="c.kind !== 'smart'" class="child-create" @click="emit('create-child', c.id)">
            ＋ 在此新建二级收藏夹
          </div>
        </template>
      </template>
      <a-empty
        v-if="collections.length === 0 && !loading"
        description="暂无合集，点击上方按钮新建"
      />
    </a-spin>
    <div class="drag-hint">拖动一级合集调整顺序；二级收藏夹由抖音同步维护</div>
  </aside>
</template>

<style scoped>
/* 左侧列表 */
.collection-list {
  width: 260px;
  min-width: 260px;
  min-height: 420px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: nowrap;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}
.collection-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 2px;
}
.collection-item:hover {
  background: #f5f7fa;
}
.collection-item.active {
  background: #eef4ff;
  border-color: #94bfff;
}
.collection-item.drag-over {
  border-top: 2px solid #2080f0;
}
/* 二级收藏夹：缩进 + 更淡的字色，视觉上从属于上面那个一级 */
.collection-item.child {
  padding-left: 26px;
}
.collection-item.child .collection-name {
  font-size: 12.5px;
  color: #4e5969;
}
.collapse-toggle {
  width: 12px;
  font-size: 10px;
  color: #86909c;
  flex: none;
  text-align: center;
}
.collapse-toggle.placeholder {
  visibility: hidden;
}
.child-create {
  padding: 4px 10px 4px 26px;
  font-size: 11px;
  color: #86909c;
  cursor: pointer;
  border-radius: 6px;
}
.child-create:hover {
  color: #2080f0;
  background: #f5f7fa;
}
.collection-kind {
  font-size: 14px;
}
.collection-info {
  flex: 1;
  min-width: 0;
}
.collection-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collection-meta {
  font-size: 11px;
  color: #86909c;
}
.drag-hint {
  font-size: 11px;
  color: #c9cdd4;
  text-align: center;
  padding-top: 8px;
}

/* 列表区撑满剩余高度，让「拖动调整」提示稳定贴底 */
.collection-list :deep(.arco-spin) {
  flex: 1;
}
</style>
