<script setup lang="ts">
/** 搜索历史条：最近搜索关键词标签 + 清除历史。 */

defineProps<{
  /** 搜索历史关键词列表 */
  history: string[]
  /** 当前关键词（有关键词时隐藏历史条） */
  keyword: string
}>()

const emit = defineEmits<{
  (e: 'apply', q: string): void
  (e: 'clear'): void
}>()
</script>

<template>
  <div v-if="history.length > 0 && !keyword" class="search-history">
    <span style="font-size:12px;color:#999">最近搜索：</span>
    <n-tag
      v-for="(h, i) in history.slice(0, 6)"
      :key="i"
      size="tiny"
      style="cursor:pointer"
      @click="emit('apply', h)"
    >
      {{ h }}
    </n-tag>
    <n-button size="tiny" text @click="emit('clear')" style="font-size:11px">清除历史</n-button>
  </div>
</template>

<style scoped>
.search-history {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
</style>
