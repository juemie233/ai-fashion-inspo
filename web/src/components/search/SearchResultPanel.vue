<script setup lang="ts">
/** 搜索结果面板：结果统计、复制链接、向量搜索横幅、排序/密度、结果网格与分页。 */

import { computed } from 'vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import type { InspirationOut } from '@/api/inspirations'

const props = defineProps<{
  /** 筛选面板是否可见 */
  filterVisible: boolean
  /** 结果总数 */
  total: number
  /** 搜索执行中 */
  searching: boolean
  /** 当前关键词 */
  keyword: string
  /** 已选标签数量 */
  selectedTagCount: number
  /** 排除标签数量 */
  excludedTagCount: number
  /** 标签组合逻辑 */
  combineMode: 'AND' | 'OR'
  /** 结果列表 */
  results: InspirationOut[]
  /** 卡片密度 */
  density: 'compact' | 'standard' | 'comfortable'
  /** 向量搜索模式 */
  vectorMode: 'none' | 'semantic' | 'image'
  /** 向量搜索查询描述 */
  vectorQueryLabel: string
  /** 以图搜图预览图 URL */
  imagePreviewUrl: string | null
  /** 向量结果卡片角标映射 */
  vectorBadges?: Record<string, string>
  /** 排序选项 */
  sortOptions: Array<{ label: string; value: string }>
  /** 当前排序 */
  sortMode: string
  /** 当前页码 */
  currentPage: number
  /** 总页数 */
  totalPages: number
  /** 每页数量 */
  pageSize: number
}>()

const emit = defineEmits<{
  (e: 'update:filterVisible', value: boolean): void
  (e: 'update:density', value: 'compact' | 'standard' | 'comfortable'): void
  (e: 'update:sortMode', value: string): void
  (e: 'copyLink'): void
  (e: 'exitVector'): void
  (e: 'delete', id: string): void
  (e: 'toggleFavorite', id: string): void
  /** 分页翻页（携带目标页码，由父级执行搜索） */
  (e: 'search', page: number): void
  (e: 'update:pageSize', value: number): void
  /** 排序变更（用于立即重新搜索） */
  (e: 'sortChange'): void
}>()

/** 筛选面板可见性（展开/收起切换） */
const filterVisibleModel = computed({
  get: () => props.filterVisible,
  set: (v: boolean) => emit('update:filterVisible', v),
})

/** 卡片密度 */
const densityModel = computed({
  get: () => props.density,
  set: (v: 'compact' | 'standard' | 'comfortable') => emit('update:density', v),
})

/** 排序模式（变更即触发重新搜索） */
const sortModeModel = computed({
  get: () => props.sortMode,
  set: (v: string) => {
    emit('update:sortMode', v)
    emit('sortChange')
  },
})

/** 页码（变更触发 doSearch(page)） */
const currentPageModel = computed({
  get: () => props.currentPage,
  set: (p: number) => emit('search', p),
})

/** 每页数量（父级在 update:pageSize 中处理回第一页并重新搜索） */
const pageSizeModel = computed({
  get: () => props.pageSize,
  set: (s: number) => emit('update:pageSize', s),
})
</script>

<template>
  <main class="result-panel">
    <n-card size="small" :bordered="true">
      <template #header>
        <div class="result-header-row">
          <span v-if="!filterVisible">
            <n-button size="tiny" type="primary" secondary @click="filterVisibleModel = true">
              展开筛选
            </n-button>
          </span>
          <span v-if="total > 0" class="result-count">
            找到 <strong>{{ total }}</strong> 条结果
          </span>
          <span v-else-if="!searching" style="color:#999">
            {{ keyword || selectedTagCount > 0
              ? '未找到匹配结果，请尝试放宽筛选条件'
              : '输入关键词或选择标签开始搜索' }}
          </span>
          <span style="flex:1" />
          <n-button size="tiny" @click="emit('copyLink')">复制搜索链接</n-button>
          <!-- 向量搜索横幅 -->
          <span v-if="vectorMode !== 'none'" class="vector-mode-banner">
            <template v-if="vectorMode === 'semantic'">语义搜索</template>
            <template v-else>以图搜图</template>「{{ vectorQueryLabel }}」
            <img v-if="imagePreviewUrl" :src="imagePreviewUrl" class="vector-query-thumb" alt="搜索图" />
            <n-button size="tiny" text type="primary" @click="emit('exitVector')">返回普通搜索</n-button>
          </span>
          <!-- 向量搜索固定取前 50 条提示 -->
          <span v-if="vectorMode !== 'none'" class="vector-limit-hint">仅显示前 50 条最相似结果</span>
          <!-- 排序 + 密度（向量搜索时不显示排序） -->
          <template v-if="vectorMode === 'none'">
            <n-select
              v-model:value="sortModeModel"
              :options="sortOptions"
              size="tiny"
              style="width:110px"
            />
          </template>
          <n-button-group size="tiny">
            <n-button :type="density==='compact'?'primary':'default'" @click="densityModel='compact'" title="紧凑">⊞</n-button>
            <n-button :type="density==='standard'?'primary':'default'" @click="densityModel='standard'" title="标准">⊟</n-button>
            <n-button :type="density==='comfortable'?'primary':'default'" @click="densityModel='comfortable'" title="宽松">⊠</n-button>
          </n-button-group>
        </div>
      </template>

      <!-- 无结果诊断 -->
      <div v-if="total === 0 && !searching && (selectedTagCount > 0 || keyword)" class="no-result-hint">
        <n-alert type="info" style="margin-bottom:12px">
          <template #header>未找到匹配结果，建议尝试：</template>
          <ul style="margin:4px 0;padding-left:16px;font-size:12px">
            <li v-if="combineMode === 'AND' && selectedTagCount > 1">
              将「全部匹配」切换为「任意匹配」模式
            </li>
            <li v-if="selectedTagCount > 1">减少已选标签数量</li>
            <li v-if="excludedTagCount > 0">减少排除标签</li>
            <li>检查关键词拼写或尝试更宽泛的词语</li>
          </ul>
        </n-alert>
      </div>

      <MasonryGrid
        :items="results"
        :loading="searching"
        :density="density"
        :badges="vectorMode !== 'none' ? vectorBadges : undefined"
        @delete="emit('delete', $event)"
        @toggle-favorite="emit('toggleFavorite', $event)"
      />

      <!-- 分页（向量搜索不翻页） -->
      <div v-if="totalPages > 1 && vectorMode === 'none'" class="pagination-wrapper">
        <n-pagination
          v-model:page="currentPageModel"
          :page-count="totalPages"
          v-model:page-size="pageSizeModel"
          show-size-picker
          :page-sizes="[25, 50, 100]"
        />
      </div>
    </n-card>
  </main>
</template>

<style scoped>
.result-panel {
  flex: 1;
  min-width: 0;
}

.result-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.result-count {
  color: #666;
  font-size: 14px;
}

.vector-mode-banner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #8b5cf6;
  background: #f5f3ff;
  border: 1px solid #ddd6fe;
  border-radius: 6px;
  padding: 4px 10px;
}

.vector-query-thumb {
  width: 32px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid #ddd;
}

.vector-limit-hint {
  font-size: 12px;
  color: #999;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
