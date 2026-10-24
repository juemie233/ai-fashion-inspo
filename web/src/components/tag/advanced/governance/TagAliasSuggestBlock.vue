<script setup lang="ts">
/** 别名建议：找出「同义但不够相似」的标签对，一键设别名或合并。
 *
 * 为什么值得做：AI 打标写入前会先做**别名归一化**（`normalize_tag_name_async`），
 * 所以把「白衬衫 / 白色衬衫」这类变体登记成别名，等于从源头掐掉未来新造的同义标签。
 * 分界线：相似度 ≥ 0.75 视为重复（合并，改素材关联）；0.60~0.75 视为同义变体
 * （设别名，不动已有素材关联，改错也容易撤）。数据与动作在 useTagGovernance。
 */

import { onMounted } from 'vue'
import { useAliasSuggestions } from '@/composables/useTagGovernance'
import { classifyPair } from '@/utils/tagGovernance'

const { loading, busy, suggestions, pairKey, load, ignore, makeAlias, doMerge } =
  useAliasSuggestions()

onMounted(load)
</script>

<template>
  <div class="alias-block">
    <a-alert type="info" :show-icon="false" style="margin-bottom: 12px">
      这里只列「相似度 0.60~0.75」的<strong>同义变体</strong>：设别名不会改已有素材关联， 只让以后
      AI 打标自动归一到主标签；真要合并素材关联请用右侧「合并」。
    </a-alert>

    <a-space :size="8" style="margin-bottom: 12px">
      <a-button size="small" :loading="loading" @click="load">重新扫描</a-button>
      <span class="hint">当前 {{ suggestions.length }} 对建议</span>
    </a-space>

    <a-spin :loading="loading" style="display: block">
      <a-empty v-if="!loading && suggestions.length === 0" description="没有同义变体建议" />
      <div v-for="pair in suggestions" :key="pairKey(pair)" class="pair-row">
        <div class="pair-names">
          <a-tag color="arcoblue" size="small">{{ pair.tag_a.name }}</a-tag>
          <span class="similarity">≈ {{ Math.round(pair.similarity * 100) }}%</span>
          <a-tag size="small">{{ pair.tag_b.name }}</a-tag>
          <span class="hint">
            {{ classifyPair(pair.similarity) === 'alias' ? '建议设别名' : '建议合并' }}
          </span>
        </div>
        <a-space :size="6">
          <a-button size="mini" type="primary" :loading="busy" @click="makeAlias(pair)">
            设为别名
          </a-button>
          <a-popconfirm
            :content="`把「${pair.tag_b.name}」合并进「${pair.tag_a.name}」？素材关联会一并指过去。`"
            @ok="doMerge(pair)"
          >
            <a-button size="mini" :loading="busy">合并</a-button>
          </a-popconfirm>
          <a-button size="mini" type="text" @click="ignore(pair)">忽略</a-button>
        </a-space>
      </div>
    </a-spin>
  </div>
</template>

<style scoped>
.alias-block {
  padding: 4px 0;
}
.pair-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border-2);
}
.pair-row:last-child {
  border-bottom: none;
}
.pair-names {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.similarity {
  color: var(--color-text-3);
  font-size: 12px;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
