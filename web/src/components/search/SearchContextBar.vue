<script setup lang="ts">
/** 当前筛选信息横幅：展示关键词、已选标签、排除标签与组合逻辑。 */

defineProps<{
  /** 搜索关键词 */
  keyword: string
  /** 已选标签名称列表 */
  selectedTags: string[]
  /** 排除标签名称列表 */
  excludedTags: string[]
  /** 组合逻辑：AND 全部匹配 / OR 任意匹配 */
  combineMode: 'AND' | 'OR'
}>()
</script>

<template>
  <div v-if="keyword || selectedTags.length > 0" class="search-context">
    搜索 <strong v-if="keyword">"{{ keyword }}"</strong>
    <span v-if="selectedTags.length > 0">
      {{ keyword ? ' · ' : '' }}{{ combineMode === 'AND' ? '全部匹配' : '任意匹配' }}：
      <n-tag v-for="n in selectedTags" :key="n" size="tiny" type="info">{{ n }}</n-tag>
    </span>
    <span v-if="excludedTags.length > 0">
      · 排除：<n-tag v-for="n in excludedTags" :key="n" size="tiny" type="error">{{ n }}</n-tag>
    </span>
  </div>
</template>

<style scoped>
.search-context {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}
</style>
