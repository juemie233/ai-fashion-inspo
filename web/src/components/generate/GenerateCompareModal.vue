<script setup lang="ts">
/** 出图对比（参考图 ↔ 生成图）：**独立**的对比视图。
 *
 * 为什么独立：候选区那套「点图看大图」是**浏览**（灯箱里左右翻、缩放、拖动，目的是
 * 一张张看清细节）。改款的真实需求是**比对**——「主体/版型/配色还像不像原图」。
 * 两者交互互相冲突（浏览要全屏、对比要固定两栏同时可见），所以这里单开一个弹窗：
 * 自己的索引、自己的键盘绑定，打开/关闭都不影响候选区的勾选与灯箱状态。
 *
 * 能力：
 * - **并排**（默认）：左参考图、右生成图，两边等高、按 contain 完整入框，便于看版型；
 * - **叠看**：两张图叠在同一框里，用透明度滑块来回压，用于核对姿态/衣长偏差；
 * - 键盘 ← → 切生成图（对比时手不用离开键盘）；
 * - 多张参考图时可直接切换（第 1 张为主参考，通常是定主体的那张）。
 */

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { fetchInspirations, getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  visible: boolean
  /** 本批候选图（url 已是后端给出的可直接用的地址） */
  candidates: {
    url: string
    seed: number
    filename: string
    imported: boolean
  }[]
  /** 打开时定位到第几张（0 开始） */
  initialIndex: number
  /** 本批用到的参考素材 id（顺序即参考顺序） */
  referenceIds: string[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
}>()

interface RefImage {
  id: string
  url: string
}

const refImages = ref<RefImage[]>([])
const refLoading = ref(false)
/** 对比模式：并排 / 叠看 */
const mode = ref<'side' | 'overlay'>('side')
/** 叠看时生成图的透明度（%） */
const overlayOpacity = ref(55)
const index = ref(0)
const refIndex = ref(0)

const count = computed(() => props.candidates.length)
const current = computed(() => props.candidates[index.value] ?? null)
const currentRef = computed(() => refImages.value[refIndex.value] ?? null)

/** 打开时：定位到指定候选 + 拉参考图详情 + 挂键盘监听；关闭时摘掉监听 */
watch(
  () => props.visible,
  async (open) => {
    if (!open) {
      window.removeEventListener('keydown', onKeydown)
      return
    }
    index.value = Math.min(Math.max(props.initialIndex, 0), Math.max(count.value - 1, 0))
    refIndex.value = 0
    mode.value = 'side'
    window.addEventListener('keydown', onKeydown)
    await loadRefs()
  },
  // immediate：调用方若一开始就 visible=true（如测试或深链），也要正常初始化
  { immediate: true },
)

async function loadRefs(): Promise<void> {
  const ids = props.referenceIds.filter(Boolean)
  if (!ids.length) {
    refImages.value = []
    return
  }
  refLoading.value = true
  try {
    const data = await fetchInspirations({ ids: ids.join(','), size: ids.length })
    const byId = new Map((data.items ?? []).map((item) => [item.id, item]))
    // 按 referenceIds 的顺序还原：第 1 张是主参考，顺序不能丢
    refImages.value = ids.flatMap((id) => {
      const item = byId.get(id)
      if (!item) return []
      return [{ id, url: getFileUrl(item.file_path) }]
    })
  } catch {
    // 参考图加载失败不该挡住对比（生成图仍可单独看），静默降级
    refImages.value = []
  } finally {
    refLoading.value = false
  }
}

function step(delta: number): void {
  if (!count.value) return
  index.value = (index.value + delta + count.value) % count.value
}

/** 键盘 ← → 切生成图：对比时手不用离开键盘（监听只在弹窗打开时挂着） */
function onKeydown(event: KeyboardEvent): void {
  if (!props.visible) return
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    step(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    step(1)
  }
}

// 组件被意外卸载时也要把监听摘掉，避免残留全局 keydown
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

const refOptions = computed(() =>
  refImages.value.map((item, i) => ({ label: `参考图 ${i + 1}`, value: i })),
)
</script>

<template>
  <a-modal
    :visible="visible"
    title="对比：参考图 ↔ 生成图"
    :width="1140"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="cmp-toolbar">
      <a-radio-group v-model="mode" size="small" type="button">
        <a-radio value="side">并排</a-radio>
        <a-radio value="overlay">叠看</a-radio>
      </a-radio-group>
      <template v-if="mode === 'overlay'">
        <span class="cmp-hint">生成图透明度</span>
        <a-slider
          v-model="overlayOpacity"
          :min="0"
          :max="100"
          :style="{ width: '140px' }"
          size="small"
        />
      </template>

      <span class="cmp-spacer" />

      <a-select
        v-if="refOptions.length > 1"
        v-model="refIndex"
        :options="refOptions"
        size="small"
        style="width: 110px"
      />
      <a-button-group size="small">
        <a-button :disabled="count <= 1" @click="step(-1)">‹ 上一张</a-button>
        <a-button :disabled="count <= 1" @click="step(1)">下一张 ›</a-button>
      </a-button-group>
      <span class="cmp-hint"> {{ count ? index + 1 : 0 }} / {{ count }} · 键盘 ← → 切换 </span>
    </div>

    <a-spin :loading="refLoading" style="display: block">
      <!-- 并排：两栏等高，各自按 contain 完整显示（看版型/衣长最直接） -->
      <div v-if="mode === 'side'" class="cmp-side">
        <div class="cmp-pane">
          <div class="cmp-pane-head">
            参考图
            <span v-if="refImages.length > 1">（{{ refIndex + 1 }} / {{ refImages.length }}）</span>
          </div>
          <img v-if="currentRef" :src="currentRef.url" alt="参考图" class="cmp-img" />
          <div v-else class="cmp-empty">
            {{ referenceIds.length ? '参考图加载失败或素材已删除' : '本批没有参考图（文生图）' }}
          </div>
        </div>
        <div class="cmp-pane">
          <div class="cmp-pane-head">
            生成图
            <span v-if="current" class="cmp-file"
              >{{ current.filename }} · seed {{ current.seed }}</span
            >
            <a-tag v-if="current?.imported" size="small" color="green">已入库</a-tag>
          </div>
          <img v-if="current" :src="current.url" alt="生成图" class="cmp-img" />
          <div v-else class="cmp-empty">还没有生成图</div>
        </div>
      </div>

      <!-- 叠看：同一个框里压透明度，用来核对姿态/位置偏差 -->
      <div v-else class="cmp-overlay">
        <img v-if="currentRef" :src="currentRef.url" alt="参考图" class="cmp-overlay-img" />
        <div v-else class="cmp-empty cmp-overlay-empty">参考图不可用</div>
        <img
          v-if="current"
          :src="current.url"
          alt="生成图"
          class="cmp-overlay-img cmp-overlay-top"
          :style="{ opacity: overlayOpacity / 100 }"
        />
      </div>
    </a-spin>

    <!-- 候选缩略图条：直接跳到想比的那张（与候选区的勾选互不影响） -->
    <div v-if="count > 1" class="cmp-strip">
      <div
        v-for="(item, i) in candidates"
        :key="item.filename"
        class="cmp-strip-item"
        :class="{ 'is-current': i === index }"
        :title="`第 ${i + 1} 张 · seed ${item.seed}`"
        @click="index = i"
      >
        <img :src="item.url" alt="" loading="lazy" />
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.cmp-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.cmp-spacer {
  flex: 1;
}
.cmp-hint {
  font-size: 12px;
  color: #8a8f99;
}
.cmp-side {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.cmp-pane {
  border: 1px solid #e5e6eb;
  border-radius: 4px;
  overflow: hidden;
  background: #f7f8fa;
}
.cmp-pane-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  font-size: 12px;
  color: #4e5969;
  background: #fff;
  border-bottom: 1px solid #e5e6eb;
}
.cmp-file {
  color: #8a8f99;
  font-size: 11px;
}
.cmp-img {
  display: block;
  width: 100%;
  height: 58vh;
  object-fit: contain;
  background: #f2f3f5;
}
.cmp-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 58vh;
  font-size: 13px;
  color: #8a8f99;
}
.cmp-overlay {
  position: relative;
  height: 62vh;
  border: 1px solid #e5e6eb;
  border-radius: 4px;
  background: #f2f3f5;
  overflow: hidden;
}
.cmp-overlay-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.cmp-overlay-empty {
  position: absolute;
  inset: 0;
  height: 100%;
}
.cmp-strip {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.cmp-strip-item {
  flex: 0 0 auto;
  width: 64px;
  height: 64px;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  background: #f7f8fa;
}
.cmp-strip-item:hover {
  border-color: #94bfff;
}
.cmp-strip-item.is-current {
  border-color: #165dff;
}
.cmp-strip-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
