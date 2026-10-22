<script setup lang="ts">
/** 合集详情头：合集名 / 父级面包屑 / 智能与抖音同步标记 / 素材数 + 操作按钮组。
 *
 * 从 CollectionsView.vue 抽出（视图只做编排）。两种合集的按钮不同：
 * 智能合集是「编辑条件 / 转手动」，手动合集是「编辑排序（保存 / 取消）/ 重命名」，
 * 「删除合集」两者共用。
 */

import type { CollectionOut } from '@/api/collections'

defineProps<{
  /** 当前选中的合集 */
  current: CollectionOut
  /** 二级收藏夹所属一级的名字（一级自己的内容区不显示面包屑） */
  parentName: string | null
  /** 当前合集的素材总数 */
  total: number
  /** 是否处于「编辑排序」模式（决定显示「编辑排序」还是「保存 / 取消」） */
  ordering: boolean
}>()

const emit = defineEmits<{
  (e: 'edit-smart'): void
  /** 把智能合集当前匹配到的内容固化为手动合集 */
  (e: 'solidify'): void
  (e: 'enter-ordering'): void
  (e: 'save-order'): void
  (e: 'cancel-ordering'): void
  (e: 'rename'): void
  (e: 'delete'): void
}>()
</script>

<template>
  <div class="content-header">
    <div class="content-title">
      <h2>{{ current.name }}</h2>
      <span v-if="parentName" class="content-parent">{{ parentName }} /</span>
      <a-tag v-if="current.kind === 'smart'" color="arcoblue" size="small">⚡ 智能合集</a-tag>
      <a-tag v-if="current.auto_source" color="gray" size="small">抖音同步</a-tag>
      <span class="content-total">共 {{ total }} 个素材</span>
    </div>
    <div class="content-actions">
      <template v-if="current.kind === 'smart'">
        <a-button size="small" @click="emit('edit-smart')">编辑条件</a-button>
        <a-popconfirm content="将当前匹配内容固化为手动合集？" @ok="emit('solidify')">
          <a-button size="small">转手动</a-button>
        </a-popconfirm>
      </template>
      <template v-else>
        <a-button v-if="!ordering" size="small" @click="emit('enter-ordering')">编辑排序</a-button>
        <template v-else>
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
  color: #999;
}
/* 二级收藏夹的父级面包屑（一级名下没有这段） */
.content-parent {
  font-size: 13px;
  color: #86909c;
}
.content-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
