<script setup lang="ts">
/** 人物素材「随机浏览」弹窗：从该人物（可带筛选）的素材里随机抽一张，一直看下去。
 *
 * 为什么需要：详情页素材可达 3,426 条，瀑布流按页翻几乎逛不完；「随机浏览」是
 * 「看看她都有什么」的最短路径。与「全屏浏览」的区别是：灯箱翻的是**当前页**的
 * 固定列表（适合细看），随机浏览每次都是新的随机结果（适合逛）。
 *
 * 交互：
 * - 「换一张」= 重新随机；「上一张」= 回到本次浏览历史（不是随机倒退，避免迷路）；
 * - 空格 / → 换一张，← 上一张，Esc 关闭；
 * - 「看大图」把当前这张交给灯箱（复用既有 ImageLightbox）。
 */

import { computed, onBeforeUnmount, watch } from 'vue'
import { getFileUrl } from '@/api/inspirations'
import type { PersonInspiration } from '@/api/persons'

const props = defineProps<{
  visible: boolean
  loading: boolean
  /** 当前随机到的素材（null = 还没抽到 / 该人物没有素材） */
  item: PersonInspiration | null
  /** 浏览历史条数（>0 才能「上一张」） */
  historyCount: number
  /** 该人物（筛选后）的素材总数：用于「从 N 张里随机」的说明 */
  total: number
  personName: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'next'): void
  (e: 'prev'): void
  (e: 'open-fullscreen', item: PersonInspiration): void
}>()

const previewSrc = computed(() => {
  const item = props.item
  if (!item) return ''
  return getFileUrl(item.thumbnail_path || item.file_path)
})

/** 大图地址：视频用原文件（浏览器直接播也能看），图片用原图 */
const isVideo = computed(() => props.item?.media_type === 'video')

const metaText = computed(() => {
  const item = props.item
  if (!item) return ''
  const parts: string[] = []
  if (item.created_at) parts.push(`入库 ${String(item.created_at).slice(0, 10)}`)
  if (item.quality_status === 'approved') parts.push('已通过审核')
  else if (item.quality_status === 'rejected') parts.push('已被拒绝')
  if (item.rating) parts.push(`评分 ${item.rating} 星`)
  if (item.is_favorite) parts.push('已收藏')
  return parts.join(' · ')
})

function onKeydown(event: KeyboardEvent): void {
  if (!props.visible) return
  const key = event.key
  if (key === 'ArrowRight' || key === ' ') {
    event.preventDefault()
    emit('next')
  } else if (key === 'ArrowLeft') {
    event.preventDefault()
    emit('prev')
  }
}

// 键盘只在弹窗打开时挂载（与出图对比弹窗同一约定：别在全局留监听）
watch(
  () => props.visible,
  (open) => {
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <a-modal
    :visible="visible"
    :title="`随机浏览 · ${personName}`"
    :width="860"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="rb-head">
      <span class="rb-hint">
        <template v-if="total > 0">从 {{ total }} 张素材里随机抽（按当前筛选）</template>
        <template v-else>该人物还没有可浏览的素材</template>
      </span>
      <span class="rb-hint">空格 / → 换一张 · ← 上一张 · Esc 关闭</span>
    </div>

    <a-spin :loading="loading" style="display: block">
      <div class="rb-stage">
        <img v-if="previewSrc && !isVideo" :src="previewSrc" alt="" class="rb-img" />
        <video
          v-else-if="previewSrc"
          :src="getFileUrl(item?.file_path || '')"
          class="rb-img"
          controls
          preload="metadata"
        />
        <div v-else class="rb-empty">
          {{ loading ? '正在随机抽取…' : '没有可浏览的素材（换个筛选条件或先采集素材）' }}
        </div>
      </div>
    </a-spin>

    <div class="rb-meta">
      <span class="rb-hint">{{ metaText }}</span>
    </div>

    <div class="rb-footer">
      <a-space>
        <a-button size="small" :disabled="historyCount === 0" @click="emit('prev')">
          ← 上一张
        </a-button>
        <a-button type="primary" size="small" :loading="loading" @click="emit('next')">
          换一张
        </a-button>
        <a-button
          v-if="item && !isVideo"
          size="small"
          :disabled="!item"
          @click="item && emit('open-fullscreen', item)"
        >
          看大图
        </a-button>
      </a-space>
      <a-button size="small" @click="emit('update:visible', false)">关闭</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.rb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.rb-hint {
  color: var(--color-text-3);
  font-size: 12px;
}
.rb-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 56vh;
  background: var(--color-fill-2);
  border-radius: 6px;
  overflow: hidden;
}
.rb-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.rb-empty {
  color: var(--color-text-3);
  font-size: 13px;
}
.rb-meta {
  min-height: 20px;
  margin-top: 6px;
}
.rb-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-2);
}
</style>
