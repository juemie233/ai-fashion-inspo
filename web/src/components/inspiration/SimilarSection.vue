<script setup lang="ts">
/** 相似素材推荐区块：相似列表展示 + 批量添加大标签。 */

import { computed } from 'vue'
import InspirationCard from './InspirationCard.vue'
import type { SimilarItemOut } from '@/api/search'

const props = defineProps<{
  /** 相似素材列表 */
  items: SimilarItemOut[]
  /** 相似推荐加载中 */
  loading: boolean
  /** 是否处于批量选择模式 */
  batchMode: boolean
  /** 批量模式下已勾选的相似素材 ID */
  batchSelectedIds: string[]
  /** 要批量添加的大标签（v-model:batch-tag-names） */
  batchTagNames: string[]
  /** 批量添加提交中 */
  batchAdding: boolean
  /** 大标签下拉选项 */
  options: { label: string; value: string }[]
  /** 相似来源中文标注函数 */
  similarSourceLabel: (source: string) => string
}>()

const emit = defineEmits<{
  (e: 'update:batchTagNames', value: string[]): void
  (e: 'enter-batch'): void
  (e: 'exit-batch'): void
  (e: 'toggle-select-all'): void
  (e: 'toggle-select', id: string): void
  (e: 'toggle-favorite', id: string): void
  (e: 'delete', id: string): void
  (e: 'batch-add'): void
}>()

/** 批量大标签双向绑定（供批量操作栏 a-select 的 v-model 使用） */
const batchTagNamesModel = computed<string[]>({
  get: () => props.batchTagNames,
  set: (val) => emit('update:batchTagNames', val),
})
</script>

<template>
  <div class="similar-section">
    <div class="similar-header">
      <h4>相似素材推荐</h4>
      <a-spin v-if="loading" :size="14" />
      <span v-else-if="items.length === 0" class="similar-empty-hint">
        暂无相似素材（需要先回填向量，或在图像向量不可用时依赖标签匹配）
      </span>
      <span v-else class="similar-count">{{ items.length }} 个</span>
      <a-button
        v-if="items.length > 0 && !batchMode"
        size="mini"
        type="secondary"
        style="margin-left: auto"
        @click="emit('enter-batch')"
      >
        批量添加大标签
      </a-button>
    </div>

    <!-- 批量添加操作栏 -->
    <div v-if="batchMode" class="batch-toolbar">
      <span class="batch-selected-count">
        已选 {{ batchSelectedIds.length }} / {{ items.length }}
      </span>
      <a-button size="mini" type="text" @click="emit('toggle-select-all')">
        {{ batchSelectedIds.length === items.length ? '取消全选' : '全选' }}
      </a-button>
      <a-select
        v-model="batchTagNamesModel"
        multiple
        allow-create
        size="small"
        placeholder="选择或输入大标签"
        :options="options"
        style="flex: 1; min-width: 200px"
      />
      <a-button
        size="small"
        type="primary"
        status="danger"
        :loading="batchAdding"
        :disabled="batchSelectedIds.length === 0 || batchTagNames.length === 0"
        @click="emit('batch-add')"
      >
        添加（{{ batchSelectedIds.length }}）
      </a-button>
      <a-button size="small" @click="emit('exit-batch')">取消</a-button>
    </div>

    <div v-if="items.length > 0" class="similar-grid">
      <InspirationCard
        v-for="item in items"
        :key="item.inspiration.id"
        :item="item.inspiration"
        :badge="`${Math.round(item.similarity * 100)}% · ${similarSourceLabel(item.match_source)}`"
        :show-actions="!batchMode"
        :selectable="batchMode"
        :selected="batchSelectedIds.includes(item.inspiration.id)"
        :show-view-button="batchMode"
        @toggle-select="emit('toggle-select', item.inspiration.id)"
        @toggle-favorite="emit('toggle-favorite', item.inspiration.id)"
        @delete="emit('delete', item.inspiration.id)"
      />
    </div>
  </div>
</template>

<style scoped>
.similar-section {
  margin-top: 32px;
  border-top: 1px solid var(--color-border-2, #eee);
  padding-top: 16px;
}

.similar-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.similar-header h4 {
  margin: 0;
  font-size: 16px;
}

.similar-empty-hint {
  font-size: 12px;
  color: #999;
}

.similar-count {
  font-size: 12px;
  color: #999;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px 12px;
  background: #fef6f7;
  border: 1px solid #f0d6dc;
  border-radius: 8px;
  flex-wrap: wrap;
}

.batch-selected-count {
  font-size: 13px;
  color: #e0465e;
  font-weight: 600;
}

.similar-grid {
  column-count: 5;
  column-gap: 12px;
}
.similar-grid :deep(.card) {
  break-inside: avoid;
  margin-bottom: 12px;
}
@media (max-width: 1200px) {
  .similar-grid {
    column-count: 4;
  }
}
@media (max-width: 900px) {
  .similar-grid {
    column-count: 3;
  }
}
@media (max-width: 600px) {
  .similar-grid {
    column-count: 2;
  }
}
</style>
