<script setup lang="ts">
/** 视频关键帧缩略图列表：横向滚动展示视频素材抽取的关键帧（供详情页使用）。 */

import { onMounted, ref, watch } from 'vue'
import { fetchKeyframes } from '@/api/keyframes'

const props = defineProps<{
  /** 素材 ID（视频素材才有关键帧） */
  inspirationId: string
}>()

/** 关键帧 URL 列表（按时间序） */
const frames = ref<string[]>([])
/** 加载中 */
const loading = ref(false)

/** 请求序号，防止素材快速切换时旧响应覆盖新数据 */
let loadSeq = 0

/** 加载关键帧列表（后端首次访问会懒提取，视频较长时可能稍慢） */
async function load(id: string) {
  if (!id) return
  const seq = ++loadSeq
  loading.value = true
  try {
    const data = await fetchKeyframes(id)
    if (seq !== loadSeq) return // 已有更新的请求，丢弃过期响应
    frames.value = data.frames || []
  } catch {
    if (seq === loadSeq) frames.value = [] // 提取失败静默降级，不打断详情页
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(() => load(props.inspirationId))

watch(
  () => props.inspirationId,
  (id) => load(id),
)

/** 点击缩略图：新标签页打开原始帧图（可另存/细看） */
function openFrame(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <div v-if="loading || frames.length > 0" class="keyframes-section">
    <h4>
      关键帧<span v-if="frames.length > 0" class="keyframe-count">（{{ frames.length }} 帧）</span>
    </h4>
    <a-skeleton v-if="loading" :loading="loading" :rows="2" active />
    <div v-else class="keyframe-strip">
      <img
        v-for="url in frames"
        :key="url"
        :src="url"
        alt="视频关键帧"
        class="keyframe-thumb"
        loading="lazy"
        @click="openFrame(url)"
      />
    </div>
  </div>
</template>

<style scoped>
.keyframes-section {
  margin-top: 16px;
}

.keyframes-section h4 {
  margin-bottom: 12px;
  font-size: 16px;
}

.keyframe-count {
  font-size: 12px;
  color: #999;
  font-weight: normal;
}

/* 横向滚动条带：帧多时不换行，横向滚动浏览 */
.keyframe-strip {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 8px;
}

.keyframe-thumb {
  height: 120px;
  flex-shrink: 0;
  border-radius: 8px;
  cursor: zoom-in;
  background: #f5f5f5;
  object-fit: cover;
}
</style>
