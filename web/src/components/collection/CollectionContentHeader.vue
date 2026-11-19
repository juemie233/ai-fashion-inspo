<script setup lang="ts">
/** 合集详情头：合集名 / 父级面包屑 / 智能与抖音同步标记 / 素材数 + 操作按钮组。
 *
 * 从 CollectionsView.vue 抽出（视图只做编排）。两种合集的按钮不同：
 * 智能合集是「编辑条件 / 转手动」，手动合集是「编辑排序（保存 / 取消）/ 重命名」，
 * 「删除合集」两者共用。
 */

import { computed } from 'vue'
import { SYSTEM_MANUAL_COLLECTION_NAME, type CollectionOut } from '@/api/collections'

const props = defineProps<{
  /** 当前选中的合集 */
  current: CollectionOut
  /** 二级收藏夹所属一级的名字（一级自己的内容区不显示面包屑） */
  parentName: string | null
  /** 当前合集的素材总数 */
  total: number
  /** 是否处于「编辑排序」模式（决定显示「编辑排序」还是「保存 / 取消」） */
  ordering: boolean
  /** 是否允许进入「编辑排序」：仅手动合集 + 手动顺序时允许；
   *  按最新/评分等排序时视图会传 false，避免拖拽结果与当前展示顺序不一致 */
  canOrder?: boolean
}>()

/** 系统收藏根只读：后端也会对 update/solidify/delete 返回 400 */
const isSystemManualRoot = computed(
  () => props.current.parent_id == null && props.current.name === SYSTEM_MANUAL_COLLECTION_NAME,
)

/** 合并入口：仅手动合集、非抖音自动同步、非系统收藏根（用户确认合并后删除源） */
const canMerge = computed(
  () => props.current.kind === 'manual' && !props.current.auto_source && !isSystemManualRoot.value,
)

const emit = defineEmits<{
  (e: 'edit-smart'): void
  /** 把智能合集当前匹配到的内容固化为手动合集 */
  (e: 'solidify'): void
  (e: 'enter-ordering'): void
  (e: 'save-order'): void
  (e: 'cancel-ordering'): void
  (e: 'rename'): void
  /** 把当前手动合集合并到另一个目标合集（合并后删除源） */
  (e: 'merge'): void
  (e: 'delete'): void
}>()
</script>

<template>
  <div class="content-header">
    <div class="content-title">
      <h2>{{ current.name }}</h2>
      <span v-if="parentName" class="content-parent">{{ parentName }} /</span>
      <a-tag v-if="current.kind === 'smart'" color="arcoblue" size="small">智能合集</a-tag>
      <a-tag v-if="isSystemManualRoot" color="gray" size="small">系统收藏 · 自动同步</a-tag>
      <a-tag v-if="current.auto_source" color="gray" size="small">抖音同步</a-tag>
      <span class="content-total">共 {{ total }} 个素材</span>
    </div>
    <div v-if="!isSystemManualRoot" class="content-actions">
      <template v-if="current.kind === 'smart'">
        <a-button size="small" @click="emit('edit-smart')">编辑条件</a-button>
        <a-popconfirm content="将当前匹配内容固化为手动合集？" @ok="emit('solidify')">
          <a-button size="small">转手动</a-button>
        </a-popconfirm>
      </template>
      <template v-else>
        <a-button
          v-if="!ordering && canOrder !== false"
          size="small"
          @click="emit('enter-ordering')"
          >编辑排序</a-button
        >
        <!-- 必须显式 v-else-if="ordering"：写成 v-else 会让「非手动排序（canOrder=false）」
             也落进这一支，显示一对点了会按旧顺序重排的「保存/取消排序」 -->
        <template v-else-if="ordering">
          <a-button size="small" type="primary" @click="emit('save-order')">保存顺序</a-button>
          <a-button size="small" @click="emit('cancel-ordering')">取消排序</a-button>
        </template>
        <a-tooltip
          :disabled="!current.auto_source"
          content="由抖音同步自动维护，名称以抖音侧的收藏夹名为准"
        >
          <a-button size="small" :disabled="!!current.auto_source" @click="emit('rename')">
            重命名
          </a-button>
        </a-tooltip>
        <a-button v-if="canMerge" size="small" @click="emit('merge')">合并到…</a-button>
      </template>
      <a-button size="small" status="danger" type="text" @click="emit('delete')">
        删除合集
      </a-button>
    </div>
  </div>
</template>

<style scoped>
.content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.content-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.content-title h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.content-total {
  font-size: 13px;
  color: var(--ink-3);
}
/* 二级收藏夹的父级面包屑（一级名下没有这段） */
.content-parent {
  font-size: 13px;
  color: var(--ink-3);
}
.content-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* ── 印样台：合集标题是这摞样张的标签 ── */
.content-header {
  align-items: flex-end;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 16px;
}

.content-title {
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.content-title h2 {
  font-size: 24px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0;
}

.content-total {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 13px;
  color: var(--ink-2);
}

.content-parent {
  color: var(--ink-3);
}

.content-actions :deep(.arco-btn) {
  border-radius: var(--radius-sm);
}
</style>
