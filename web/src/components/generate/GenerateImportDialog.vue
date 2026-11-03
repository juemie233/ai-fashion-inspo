<script setup lang="ts">
/**
 * 生成历史「入库」弹窗：候选图多张时让用户确认要入库哪几张（默认全选）。
 *
 * 交互约定（用户要求）：
 * - 操作列的「入库」默认= 全部入库：只有 1 张可入库时由历史表直接提交，不弹窗；
 * - 多张时弹这个对话框，**默认全选**，用户可取消勾选部分图；
 * - 弹窗里的图同样支持悬停自动放大预览；
 * - 已入库的图不可再选（后端按内容哈希去重，选了只会 skipped）。
 */

import { computed, ref, watch } from 'vue'
import { candidateUrl, type GenerationCandidate } from '@/api/generate'
import ImageHoverPreview from '@/components/common/ImageHoverPreview.vue'

const props = defineProps<{
  visible: boolean
  /** 批次 id（仅用于标题展示） */
  runId: number | null
  /** 该批候选图（含 imported 标记） */
  candidates: GenerationCandidate[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'confirm', filenames: string[]): void
}>()

/** 可选（未入库）的候选图 */
const selectable = computed(() => props.candidates.filter((item) => !item.imported))

const selected = ref<string[]>([])

/** 每次打开：默认全选所有未入库候选（用户要的是「默认全部入库」） */
watch(
  () => [props.visible, props.candidates] as const,
  ([visible]) => {
    if (!visible) return
    selected.value = selectable.value.map((item) => item.filename)
  },
  { immediate: true },
)

function isSelected(filename: string): boolean {
  return selected.value.includes(filename)
}

function toggle(filename: string): void {
  selected.value = isSelected(filename)
    ? selected.value.filter((name) => name !== filename)
    : [...selected.value, filename]
}

/** 全选/清空只作用于「未入库」的图 */
function selectAll(): void {
  selected.value = selectable.value.map((item) => item.filename)
}

function clearAll(): void {
  selected.value = []
}

function close(): void {
  emit('update:visible', false)
}

function confirm(): void {
  if (!selected.value.length) return
  // 按候选图顺序回抛：勾选先后顺序不该影响入库顺序（界面/日志可复现）
  const order = props.candidates.map((item) => item.filename)
  const filenames = [...selected.value].sort(
    (left, right) => order.indexOf(left) - order.indexOf(right),
  )
  emit('confirm', filenames)
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="`入库候选图 · 批次 #${runId ?? ''}`"
    :width="900"
    :footer="false"
    unmount-on-close
    @cancel="close"
  >
    <div class="gid-hint">
      默认已全选；取消勾选的图不会入库（已入库的不可重复选，后端按内容哈希去重）。鼠标移到图上可放大预览。
    </div>

    <div v-if="selectable.length" class="gid-grid">
      <div
        v-for="item in candidates"
        :key="item.filename"
        class="gid-card"
        :class="{ 'is-imported': item.imported, 'is-selected': isSelected(item.filename) }"
        @click="!item.imported && toggle(item.filename)"
      >
        <image-hover-preview
          :src="candidateUrl(runId ?? 0, item.filename, 384)"
          :preview-src="candidateUrl(runId ?? 0, item.filename, 1024)"
          :alt="item.filename"
          img-class="gid-img"
        />
        <span class="gid-seed">seed {{ item.seed }}</span>
        <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
        <a-checkbox
          v-else
          class="gid-check"
          :model-value="isSelected(item.filename)"
          @click.stop
          @change="toggle(item.filename)"
        />
      </div>
    </div>
    <div v-else class="gid-hint">这批候选图都已入库或已不存在，没有可入库的图。</div>

    <div class="gid-actions">
      <span class="gid-count">
        已选 <b>{{ selected.length }}</b> / {{ selectable.length }} 张
      </span>
      <a-button size="small" @click="selectAll">全选</a-button>
      <a-button size="small" @click="clearAll">清空</a-button>
      <a-button size="small" @click="close">取消</a-button>
      <a-button type="primary" size="small" :disabled="!selected.length" @click="confirm">
        确认入库
      </a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.gid-hint {
  margin-bottom: 10px;
  color: var(--color-text-3);
  font-size: 12px;
}

.gid-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  max-height: 58vh;
  overflow-y: auto;
}

.gid-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px;
  border: 2px solid transparent;
  border-radius: 8px;
  background: var(--color-fill-2);
  cursor: pointer;
}

.gid-card.is-selected {
  border-color: rgb(var(--primary-6));
}

.gid-card.is-imported {
  opacity: 0.65;
  cursor: default;
}

.gid-card :deep(.gid-img) {
  width: 100%;
  height: 200px;
  object-fit: contain;
  background: var(--color-fill-3);
  border-radius: 4px;
}

.gid-seed {
  color: var(--color-text-3);
  font-size: 11px;
}

.gid-check {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 2px;
  border-radius: 4px;
  background: rgb(255 255 255 / 85%);
}

.gid-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border-2);
}

.gid-count {
  margin-right: auto;
  font-size: 13px;
}

@media (max-width: 720px) {
  .gid-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
