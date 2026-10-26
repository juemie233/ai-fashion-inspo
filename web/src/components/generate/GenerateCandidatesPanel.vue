<script setup lang="ts">
/** 出图候选区：批次状态条、候选图网格（点图看大图 / 角标勾选 / 换 seed）与批量入库操作。
 *
 * 从 GenerateView.vue 抽出（视图只做编排）。本组件不持有任何批次状态：run、候选图与
 * 已选集合都来自 useImageGen，所有按钮一律 emit 回抛，视图接到同一批动作上。
 */

import { computed } from 'vue'
import type { GenerationCandidate, GenerationRun } from '@/api/generate'

const props = defineProps<{
  /** 当前批次（null = 还没生成过） */
  run: GenerationRun | null
  /** 本批候选图 */
  candidates: GenerationCandidate[]
  /** 已勾选的候选文件名 */
  selected: Set<string>
  /** 阶段文案（后端进度 / 错误说明，直接展示） */
  stageText: string
  /** 本次 seed 基值（0 = 随机时后端返回真实基值；为空则不显示） */
  lastSeed: number | null
  /** 提交中（换 seed 按钮的 loading） */
  submitting: boolean
}>()

const emit = defineEmits<{
  /** 点缩略图看大图（传索引，由视图打开灯箱） */
  (e: 'preview', index: number): void
  (e: 'toggle', filename: string): void
  (e: 'regenerate', item: GenerationCandidate): void
  (e: 'select-all'): void
  (e: 'clear-selection'): void
  (e: 'confirm'): void
  (e: 'discard'): void
  /** 打开「对比」视图（参数为候选图索引）；与「点图看大图」是两条独立路径 */
  (e: 'compare', index: number): void
}>()

const selectedCount = computed(() => props.selected.size)
</script>

<template>
  <div class="gen-result-head">
    <span class="gen-label" style="margin: 0">生成结果</span>
    <span v-if="lastSeed" class="gen-hint">本次 seed 基值 {{ lastSeed }}</span>
  </div>

  <div v-if="!run" class="gen-empty">还没有生成结果：左侧填好描述后点「生成」。</div>
  <template v-else>
    <a-alert
      :type="run.status === 'failed' ? 'error' : run.status === 'done' ? 'success' : 'info'"
      class="gen-alert"
    >
      {{ stageText }}
    </a-alert>

    <div v-if="run.status === 'done' && candidates.length" class="gen-toolbar">
      <a-button size="mini" @click="emit('select-all')">全选未入库</a-button>
      <a-button size="mini" @click="emit('clear-selection')">清空选择</a-button>
      <span class="gen-hint">已选 {{ selectedCount }} / {{ candidates.length }}</span>
      <span class="gen-hint">点图片看大图；「对比」与参考图并排/叠看</span>
      <a-button v-if="run.kind === 'i2i'" size="mini" @click="emit('compare', 0)">
        对比参考图
      </a-button>
      <a-button type="primary" size="mini" :disabled="selectedCount === 0" @click="emit('confirm')">
        确认入库
      </a-button>
      <a-button size="mini" status="danger" @click="emit('discard')">丢弃本批</a-button>
    </div>

    <div v-if="candidates.length" class="gen-candidates">
      <div
        v-for="(item, index) in candidates"
        :key="item.filename"
        class="gen-item"
        :class="{ 'is-selected': selected.has(item.filename) }"
      >
        <img
          :src="item.url"
          :alt="`第 ${index + 1} 张，seed ${item.seed}`"
          loading="lazy"
          title="点击查看大图"
          @click="emit('preview', index)"
        />
        <!-- 点图片是「看大图」，勾选单独给角标：候选区最常做的是放大看细节，
             把整块点击让给灯箱，入库走这个勾选框 -->
        <a-checkbox
          v-if="!item.imported"
          class="gen-item-check"
          :model-value="selected.has(item.filename)"
          title="勾选后可确认入库"
          @click.stop
          @change="emit('toggle', item.filename)"
        />
        <div class="gen-item-meta">
          <span>seed {{ item.seed }}</span>
          <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
          <span class="gen-item-actions">
            <a-button
              v-if="run.kind === 'i2i'"
              size="mini"
              type="text"
              title="与参考图并排/叠看（不改动勾选与大图浏览）"
              @click.stop="emit('compare', index)"
            >
              对比
            </a-button>
            <a-button
              v-if="!item.imported"
              size="mini"
              type="text"
              :loading="submitting"
              title="用新 seed 重出这一张"
              @click.stop="emit('regenerate', item)"
            >
              换 seed
            </a-button>
          </span>
        </div>
      </div>
    </div>
    <div v-else-if="run.status === 'done'" class="gen-empty">
      本批没有产出（可能已丢弃或全部失败）。
    </div>
  </template>
</template>

<style scoped>
/* 下面 .gen-alert / .gen-hint / .gen-label / .gen-result-head / .gen-empty
   在视图里另有一份：它们同时被环境提示与历史卡片用到，scoped 样式不能跨组件继承，
   故各自保留。 */
.gen-alert {
  margin-bottom: 12px;
}
.gen-hint {
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}
.gen-label {
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.gen-result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
.gen-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.gen-candidates {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.gen-item {
  position: relative;
  border: 2px solid transparent;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  background: #f7f8fa;
}
.gen-item:hover {
  border-color: #94bfff;
}
.gen-item.is-selected {
  border-color: #165dff;
}
.gen-item img {
  width: 100%;
  display: block;
  cursor: zoom-in;
}
/* 勾选框浮在缩略图右上角：点图片看大图，勾选才入库 */
.gen-item-check {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 2;
  padding: 2px 4px;
  border-radius: 4px;
  background: rgb(255 255 255 / 85%);
}
.gen-item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px;
  font-size: 11px;
  color: #8a8f99;
}
.gen-item-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
</style>
