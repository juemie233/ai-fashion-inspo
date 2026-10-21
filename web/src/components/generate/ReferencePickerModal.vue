<script setup lang="ts">
/** 参考图选择器：从素材库多选 1~10 张作为出图参考。
 *
 * 为什么需要它：原先只能粘素材 ID（用户得先去别处查 id 再回来贴）。图生图的核心交互
 * 就是「挑几张图」，这一步应当是点选而不是抄 id。
 *
 * 实现取舍：
 * - 只做**浏览 + 多选**，不做上传/编辑（素材先进库，再回来选）；
 * - 复用现有 `fetchInspirations` + `getFileUrl`，不新增接口；
 * - 上限 10 由父组件传 `max`，超出时勾选被忽略并提示（Qwen 2.1 官方上限）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { fetchInspirations, getFileUrl } from '@/api/inspirations'

const props = defineProps<{
  visible: boolean
  /** 已选中的素材 id（打开时回填） */
  selectedIds: string[]
  /** 最多可选张数（Qwen 2.1 上限 10） */
  max?: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'confirm', ids: string[]): void
}>()

interface PickedItem {
  id: string
  file_path: string | null
  thumbnail_path: string | null
  source_type: string
}

const items = ref<PickedItem[]>([])
const loading = ref(false)
const page = ref(1)
const hasMore = ref(false)
const picked = ref<Set<string>>(new Set())
const maxCount = computed(() => props.max ?? 10)

/** 打开时：回填已选 + 拉第一页 */
watch(
  () => props.visible,
  (open) => {
    if (!open) return
    picked.value = new Set(props.selectedIds)
    if (!items.value.length) void load(1)
  },
)

async function load(target: number): Promise<void> {
  loading.value = true
  try {
    const data = await fetchInspirations({ page: target, size: 40, media_type: 'image' })
    const list = (data.items ?? []) as PickedItem[]
    items.value = target === 1 ? list : [...items.value, ...list]
    page.value = target
    hasMore.value = list.length >= 40
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '加载素材失败')
  } finally {
    loading.value = false
  }
}

function toggle(id: string): void {
  const next = new Set(picked.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    if (next.size >= maxCount.value) {
      Message.warning(`最多选 ${maxCount.value} 张参考图`)
      return
    }
    next.add(id)
  }
  picked.value = next
}

function confirm(): void {
  if (!picked.value.size) {
    Message.warning('请至少选择一张参考图')
    return
  }
  emit('confirm', [...picked.value])
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="visible"
    title="选择参考图"
    :width="720"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="pick-head">
      <span class="pick-tip">
        参考图决定「主体与版型」，描述写「要改成什么」；已选
        <b>{{ picked.size }}</b> / {{ maxCount }}
      </span>
      <a-button size="mini" @click="emit('update:visible', false)">取消</a-button>
      <a-button type="primary" size="mini" :disabled="!picked.size" @click="confirm">
        确定
      </a-button>
    </div>

    <a-spin :loading="loading" style="display: block">
      <div class="pick-grid">
        <div
          v-for="item in items"
          :key="item.id"
          class="pick-item"
          :class="{ 'is-picked': picked.has(item.id) }"
          @click="toggle(item.id)"
        >
          <img
            :src="getFileUrl(item.thumbnail_path || item.file_path || '')"
            alt=""
            loading="lazy"
          />
          <span v-if="picked.has(item.id)" class="pick-mark">✓</span>
        </div>
      </div>
      <div v-if="!items.length && !loading" class="pick-empty">素材库里还没有可用图片</div>
      <div v-if="hasMore" class="pick-more">
        <a-button size="mini" :loading="loading" @click="load(page + 1)">加载更多</a-button>
      </div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.pick-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.pick-tip {
  flex: 1;
  font-size: 12px;
  color: #8a8f99;
}
.pick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 6px;
  max-height: 460px;
  overflow-y: auto;
}
.pick-item {
  position: relative;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  background: #f7f8fa;
}
.pick-item:hover {
  border-color: #94bfff;
}
.pick-item.is-picked {
  border-color: #165dff;
}
.pick-item img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
}
.pick-mark {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #165dff;
  color: #fff;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
}
.pick-empty {
  padding: 32px 0;
  text-align: center;
  color: #8a8f99;
  font-size: 13px;
}
.pick-more {
  margin-top: 10px;
  text-align: center;
}
</style>
