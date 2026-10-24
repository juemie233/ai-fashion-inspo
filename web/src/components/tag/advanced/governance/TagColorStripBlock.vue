<script setup lang="ts">
/** 颜色前缀剥离（治理）：先 dry-run 预览，再执行。
 *
 * 为什么值得做：AI 历史打标把颜色写进单品名，形成「颜色×品类×细节」的笛卡尔积——
 * 实测「黑色尖头细跟高跟鞋」有 1937 条关联，而「白色尖头细跟高跟鞋」只有 241 条。
 * 后端 `tag_color_strip` 已经把剥离+撞名合并+补建颜色关联写好了（单事务、写操作历史
 * 可回滚），这里只补一个「先看再动手」的入口。数据与动作在 useTagGovernance。
 */

import { computed } from 'vue'
import { useColorStrip } from '@/composables/useTagGovernance'
import { CATEGORY_LABELS } from '@/constants/tag'

const { category, previewing, applying, preview, actionable, runPreview, runApply } =
  useColorStrip()

const categoryOptions = computed(() =>
  Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
)

const ACTION_LABELS: Record<string, string> = {
  rename: '重命名',
  merge: '合并',
  skip: '跳过',
}
</script>

<template>
  <div class="strip-block">
    <a-alert type="info" :show-icon="false" style="margin-bottom: 12px">
      把「黑色尖头细跟高跟鞋」这类标签里的<strong>颜色前缀剥掉</strong>，与已有标签合并，
      并给相关素材补建对应的颜色标签关联。执行会写操作历史，可在「历史记录」里回滚。
    </a-alert>

    <a-space :size="10" wrap>
      <a-select v-model="category" :options="categoryOptions" style="width: 160px" />
      <a-button type="primary" :loading="previewing" @click="runPreview">预览影响面</a-button>
      <a-popconfirm
        :content="
          `执行颜色剥离？将重命名 ${preview?.renamed ?? 0} 个、合并 ${preview?.merged ?? 0} 个标签，` +
          `重指向 ${preview?.links_repointed ?? 0} 条关联（会写历史，可回滚）。`
        "
        :disabled="!preview || actionable === 0"
        @ok="runApply"
      >
        <a-button status="warning" :disabled="!preview || actionable === 0" :loading="applying">
          执行剥离
        </a-button>
      </a-popconfirm>
    </a-space>

    <div v-if="preview" class="preview">
      <a-descriptions :column="4" size="small" bordered style="margin-top: 12px">
        <a-descriptions-item label="扫描标签">{{ preview.total_scanned }}</a-descriptions-item>
        <a-descriptions-item label="将重命名">{{ preview.renamed }}</a-descriptions-item>
        <a-descriptions-item label="将合并">{{ preview.merged }}</a-descriptions-item>
        <a-descriptions-item label="跳过">{{ preview.skipped }}</a-descriptions-item>
        <a-descriptions-item label="重指向关联">
          {{ preview.links_repointed }}
        </a-descriptions-item>
        <a-descriptions-item label="补建颜色关联">
          {{ preview.color_links_added }}
        </a-descriptions-item>
        <a-descriptions-item label="颜色词表">{{ preview.color_prefixes }}</a-descriptions-item>
        <a-descriptions-item label="别名/跨类跳过">
          {{ preview.alias_skipped }} / {{ preview.cross_category_skipped }}
        </a-descriptions-item>
      </a-descriptions>

      <a-table
        v-if="preview.samples.length"
        :data="preview.samples"
        :pagination="false"
        size="small"
        :bordered="false"
        style="margin-top: 12px"
        :columns="[
          { title: '原标签名', dataIndex: 'old' },
          { title: '剥离后', dataIndex: 'new' },
          { title: '动作', dataIndex: 'action', width: 100 },
        ]"
      >
        <template #action="{ record }">
          {{ ACTION_LABELS[record.action] || record.action }}
        </template>
      </a-table>
      <div class="hint">样例仅展示前 20 条</div>
    </div>
  </div>
</template>

<style scoped>
.strip-block {
  padding: 4px 0;
}
.preview {
  margin-top: 4px;
}
.hint {
  margin-top: 8px;
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
