<script setup lang="ts">
/** 标签治理面板：颜色剥离 / 长尾 / 别名建议 / 新标签观察，四件事放一处。
 *
 * 为什么合到一个面板：它们解决的是同一个问题——**标签库 22,813 个、99.9% 由 AI 自动
 * 生成、70% 只被用过 1~2 次**。分开放会让人以为有四个独立毛病，其实是一条链：
 * 源头控新（观察名单）→ 归一化（别名）→ 压缩存量（长尾/颜色剥离）。
 */

import { ref } from 'vue'
import TagColorStripBlock from './governance/TagColorStripBlock.vue'
import TagLongTailBlock from './governance/TagLongTailBlock.vue'
import TagAliasSuggestBlock from './governance/TagAliasSuggestBlock.vue'
import TagWatchBlock from './governance/TagWatchBlock.vue'

const active = ref('watch')
</script>

<template>
  <div class="gov-panel">
    <a-tabs v-model:active-key="active" type="line">
      <a-tab-pane key="watch" title="新标签观察">
        <TagWatchBlock />
      </a-tab-pane>
      <a-tab-pane key="alias" title="别名建议">
        <TagAliasSuggestBlock />
      </a-tab-pane>
      <a-tab-pane key="tail" title="长尾标签">
        <TagLongTailBlock />
      </a-tab-pane>
      <a-tab-pane key="color" title="颜色剥离">
        <TagColorStripBlock />
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.gov-panel {
  padding: 0 2px;
}
</style>
