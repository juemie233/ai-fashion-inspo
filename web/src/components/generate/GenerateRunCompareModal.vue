<script setup lang="ts">
/** 两批并排对比：左 = 先选的那批，右 = 后选的那批。
 *
 * 为什么单独一个弹窗而不是复用「候选 ↔ 参考图」的对比：那个是**同一批**里的生成图
 * 与参考图对照；这里是**两批不同参数**之间的对照，关注点不同（seed/参数/风格变化），
 * 硬塞进一个组件会让两套状态互相干扰。
 */

import { computed, ref, watch } from 'vue'
import { fetchRun, type GenerationCandidate, type GenerationRun } from '@/api/generate'
import { formatDateTime, shortModelLabel } from '@/utils/format'

const props = defineProps<{
  visible: boolean
  /** 两批的 id：[左, 右]；不足两个时不请求 */
  runIds: [number, number] | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

const loading = ref(false)
const left = ref<{ run: GenerationRun | null; items: GenerationCandidate[] }>({
  run: null,
  items: [],
})
const right = ref<{ run: GenerationRun | null; items: GenerationCandidate[] }>({
  run: null,
  items: [],
})
/** 两边各自选中的候选（默认第一张） */
const pickedLeft = ref<string | null>(null)
const pickedRight = ref<string | null>(null)

function pick(items: GenerationCandidate[], filename: string | null): GenerationCandidate | null {
  if (!items.length) return null
  return items.find((item) => item.filename === filename) ?? items[0] ?? null
}

async function load(
  id: number,
): Promise<{ run: GenerationRun | null; items: GenerationCandidate[] }> {
  try {
    const detail = await fetchRun(id)
    return { run: detail.run ?? null, items: detail.items ?? [] }
  } catch {
    return { run: null, items: [] }
  }
}

watch(
  () => [props.visible, props.runIds] as const,
  async ([open, ids]) => {
    if (!open || !ids) return
    loading.value = true
    pickedLeft.value = null
    pickedRight.value = null
    try {
      const [l, r] = await Promise.all([load(ids[0]), load(ids[1])])
      left.value = l
      right.value = r
      pickedLeft.value = l.items[0]?.filename ?? null
      pickedRight.value = r.items[0]?.filename ?? null
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const leftPicked = computed(() => pick(left.value.items, pickedLeft.value))
const rightPicked = computed(() => pick(right.value.items, pickedRight.value))

function modelLabel(key: string | undefined): string {
  return key ? shortModelLabel(key) : '—'
}

function promptBrief(run: GenerationRun | null): string {
  const text = (run?.prompt ?? '').trim()
  return text.length > 80 ? `${text.slice(0, 80)}…` : text || '—'
}
</script>

<template>
  <a-modal
    :visible="visible"
    title="两批对比"
    :width="1080"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <a-spin :loading="loading" style="display: block">
      <div class="gc-grid">
        <section class="gc-col">
          <header class="gc-head">
            <b>#{{ runIds?.[0] ?? '—' }}</b>
            <span>{{ modelLabel(left.run?.model_key) }}</span>
            <span v-if="left.run?.created_at" class="gc-time">
              {{ formatDateTime(left.run.created_at) }}
            </span>
          </header>
          <p class="gc-prompt">{{ promptBrief(left.run) }}</p>
          <div class="gc-thumbs">
            <button
              v-for="item in left.items"
              :key="item.filename"
              type="button"
              class="gc-thumb"
              :class="{ 'is-picked': item.filename === pickedLeft }"
              @click="pickedLeft = item.filename"
            >
              <img :src="item.url" alt="" loading="lazy" />
              <span class="gc-seed">seed {{ item.seed }}</span>
            </button>
          </div>
          <div class="gc-stage">
            <img v-if="leftPicked" :src="leftPicked.url" alt="" />
            <div v-else class="gc-empty">这批没有候选图</div>
          </div>
        </section>

        <section class="gc-col">
          <header class="gc-head">
            <b>#{{ runIds?.[1] ?? '—' }}</b>
            <span>{{ modelLabel(right.run?.model_key) }}</span>
            <span v-if="right.run?.created_at" class="gc-time">
              {{ formatDateTime(right.run.created_at) }}
            </span>
          </header>
          <p class="gc-prompt">{{ promptBrief(right.run) }}</p>
          <div class="gc-thumbs">
            <button
              v-for="item in right.items"
              :key="item.filename"
              type="button"
              class="gc-thumb"
              :class="{ 'is-picked': item.filename === pickedRight }"
              @click="pickedRight = item.filename"
            >
              <img :src="item.url" alt="" loading="lazy" />
              <span class="gc-seed">seed {{ item.seed }}</span>
            </button>
          </div>
          <div class="gc-stage">
            <img v-if="rightPicked" :src="rightPicked.url" alt="" />
            <div v-else class="gc-empty">这批没有候选图</div>
          </div>
        </section>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.gc-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.gc-col {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.gc-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-2);
}
.gc-time {
  margin-left: auto;
  color: var(--color-text-3);
  font-size: 12px;
  white-space: nowrap;
}
.gc-prompt {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-3);
  min-height: 38px;
}
.gc-thumbs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.gc-thumb {
  position: relative;
  flex: 0 0 auto;
  width: 64px;
  height: 64px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  background: var(--color-fill-2);
  cursor: pointer;
}
.gc-thumb.is-picked {
  border-color: rgb(var(--primary-6));
}
.gc-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.gc-seed {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 10px;
  line-height: 14px;
  text-align: center;
}
.gc-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 44vh;
  background: var(--color-fill-2);
  border-radius: 6px;
  overflow: hidden;
}
.gc-stage img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.gc-empty {
  color: var(--color-text-3);
  font-size: 12px;
}
</style>
