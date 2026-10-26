<script setup lang="ts">
/** 参考图选择器：从素材库**浏览**并多选 1~10 张作为出图参考。
 *
 * 为什么需要它：图生图的核心交互就是「挑几张图」，这一步应当是点选而不是抄素材 id。
 *
 * 浏览能力（2026-10 补强，原先只有「最近 40 张 + 加载更多」）：
 * - **关键词搜索**：走 /search 的 keyword（与素材库搜索同一套口径：描述/标签/博主等）；
 * - **来源筛选**：抖音/小红书/手动上传…，不用先记住 id 再去别处查；
 * - **翻到底**：每页 40 张、可连续「加载更多」，并在切换条件时回到第 1 页；
 * - **选中顺序可见**：参考图顺序会影响生成（第 1 张是主参考），所以角标编号 + 底部
 *   已选条带，点 × 可按顺序移除。
 *
 * 实现取舍：只做浏览 + 多选（素材先进库、再回来选），不在这里上传；复用既有
 * `searchInspirations` + `getFileUrl`，不新增接口。上限由父组件传 `max`（Qwen 2.1 = 10）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { searchInspirations } from '@/api/search'
import { getFileUrl } from '@/api/inspirations'
import { buildSourceOptions, sourceLabel } from '@/utils/sourceLabel'

const props = defineProps<{
  visible: boolean
  /** 已选中的素材 id（打开时按这个顺序回填） */
  selectedIds: string[]
  /** 最多可选张数（Qwen 2.1 上限 10） */
  max?: number
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'confirm', ids: string[]): void
}>()

interface PickItem {
  id: string
  file_path: string
  thumbnail_path?: string | null
  source_type?: string
}

/** 每页张数：40 张 4 列 ≈ 10 行，一屏能看两页的量 */
const PAGE_SIZE = 40

const items = ref<PickItem[]>([])
const loading = ref(false)
const page = ref(1)
const hasMore = ref(false)
const picked = ref<string[]>([])
/** 打开时回填的 id → 详情，用于「已选」条带的缩略图与来源文案 */
const pickedMeta = ref<Map<string, PickItem>>(new Map())

const keyword = ref('')
const sourceType = ref('')
const sourceOptions = buildSourceOptions('')
const maxCount = computed(() => props.max ?? 10)
const hitMax = computed(() => picked.value.length >= maxCount.value)

/** 打开时：回填已选 + **清掉上次的筛选** + 拉第一页（搜索词/来源不跨次残留） */
watch(
  () => props.visible,
  (open) => {
    if (!open) return
    picked.value = [...props.selectedIds]
    keyword.value = ''
    sourceType.value = ''
    void load(1)
  },
)

async function load(target: number): Promise<void> {
  loading.value = true
  try {
    const data = await searchInspirations({
      keyword: keyword.value.trim() || undefined,
      source_type: sourceType.value || undefined,
      media_type: 'image',
      page: target,
      size: PAGE_SIZE,
    })
    const list = (data.items ?? []) as unknown as PickItem[]
    items.value = target === 1 ? list : [...items.value, ...list]
    page.value = target
    hasMore.value = list.length >= PAGE_SIZE
    // 回填的已选素材可能不在这页结果里（搜索词换了），用返回项补全它们的缩略图
    const meta = new Map(pickedMeta.value)
    for (const item of list) {
      if (picked.value.includes(item.id)) meta.set(item.id, item)
    }
    pickedMeta.value = meta
  } catch (e) {
    Message.error(e instanceof Error ? e.message : '加载素材失败')
  } finally {
    loading.value = false
  }
}

/** 关键词搜索：从第 1 页开始。
 *
 * 参数取 a-input-search 直接给的「输入框当前值」（Arco 会把它作为第一个参数传出来），
 * 不再只依赖 v-model 的刷新时机——两者读到的不一致时（回车很快）会搜到旧词。
 */
function onKeywordSearch(value?: unknown): void {
  if (typeof value === 'string') keyword.value = value
  void load(1)
}

/** 来源筛选变化：只重拉列表。
 *
 * **不能复用** onKeywordSearch：Arco `a-select` 的 change 第一参是**选中值**（如 `douyin`），
 * 传进来会被当成搜索词写进 keyword，查询就变成「关键词=来源值」（结果为空或完全错）。
 */
function onSourceChange(): void {
  void load(1)
}

/** 点选/取消：保持插入顺序（参考图顺序就是生成的参考顺序） */
function toggle(item: PickItem): void {
  const index = picked.value.indexOf(item.id)
  if (index >= 0) {
    picked.value = picked.value.filter((id) => id !== item.id)
    return
  }
  if (hitMax.value) {
    Message.warning(`最多选 ${maxCount.value} 张参考图`)
    return
  }
  picked.value = [...picked.value, item.id]
  pickedMeta.value = new Map(pickedMeta.value).set(item.id, item)
}

function removeAt(id: string): void {
  picked.value = picked.value.filter((x) => x !== id)
}

/** 已选顺序号（1 开始；未选返回 0） */
function orderOf(id: string): number {
  return picked.value.indexOf(id) + 1
}

/** 已选条带的一项（详情缺失时返回空串 → 模板走占位，而不是渲染 `/api/files/` 破图） */
function pickedThumb(id: string): string {
  const item = pickedMeta.value.get(id)
  const path = item?.thumbnail_path || item?.file_path
  return path ? getFileUrl(path) : ''
}

function pickedLabel(id: string): string {
  const item = pickedMeta.value.get(id)
  return item?.source_type ? sourceLabel(item.source_type) : id.slice(0, 8)
}

function confirm(): void {
  if (!picked.value.length) {
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
    :width="880"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="pick-head">
      <a-input-search
        v-model="keyword"
        placeholder="搜索素材：标签 / 博主 / 文件名"
        allow-clear
        size="small"
        style="width: 240px"
        @search="onKeywordSearch"
      />
      <a-select
        v-model="sourceType"
        :options="sourceOptions"
        size="small"
        style="width: 130px"
        @change="onSourceChange"
      />
      <span class="pick-tip">
        已选 <b>{{ picked.length }}</b> / {{ maxCount }}（顺序即参考顺序，第 1 张为主参考）
      </span>
      <a-button size="mini" @click="emit('update:visible', false)">取消</a-button>
      <a-button type="primary" size="mini" :disabled="!picked.length" @click="confirm">
        确定
      </a-button>
    </div>

    <!-- 已选条带：顺序可见、可直接移除（比「回头在网格里找那张图」快得多） -->
    <div v-if="picked.length" class="pick-strip">
      <div v-for="id in picked" :key="id" class="pick-chip" :title="pickedLabel(id)">
        <span class="pick-chip-no">{{ orderOf(id) }}</span>
        <img v-if="pickedThumb(id)" :src="pickedThumb(id)" alt="" />
        <span v-else class="pick-chip-x" />
        <button class="pick-chip-del" title="移除" @click="removeAt(id)">×</button>
      </div>
    </div>

    <a-spin :loading="loading" style="display: block">
      <div class="pick-grid">
        <div
          v-for="item in items"
          :key="item.id"
          class="pick-item"
          :class="{ 'is-picked': orderOf(item.id) > 0 }"
          :title="`${sourceLabel(item.source_type || '')} · 点击${orderOf(item.id) ? '取消选择' : '选为参考图'}`"
          @click="toggle(item)"
        >
          <img
            :src="getFileUrl(item.thumbnail_path || item.file_path || '')"
            alt=""
            loading="lazy"
          />
          <span v-if="orderOf(item.id)" class="pick-mark">{{ orderOf(item.id) }}</span>
        </div>
      </div>
      <div v-if="!items.length && !loading" class="pick-empty">
        {{
          keyword || sourceType ? '没有匹配的素材：换个关键词或来源试试' : '素材库里还没有可用图片'
        }}
      </div>
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
  flex-wrap: wrap;
}
.pick-tip {
  flex: 1;
  font-size: 12px;
  color: #8a8f99;
}
.pick-strip {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px;
  margin-bottom: 10px;
  background: #f7f8fa;
  border-radius: 4px;
}
.pick-chip {
  position: relative;
  width: 54px;
  height: 54px;
  border-radius: 4px;
  overflow: hidden;
  background: #e5e6eb;
}
.pick-chip img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.pick-chip-no {
  position: absolute;
  top: 2px;
  left: 2px;
  z-index: 2;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border-radius: 8px;
  background: rgb(22 93 255 / 90%);
  color: #fff;
  font-size: 10px;
  line-height: 15px;
  text-align: center;
}
.pick-chip-del {
  position: absolute;
  top: 2px;
  right: 2px;
  z-index: 2;
  width: 15px;
  height: 15px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 11px;
  line-height: 15px;
  cursor: pointer;
}
.pick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 6px;
  max-height: 430px;
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
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9px;
  background: #165dff;
  color: #fff;
  font-size: 11px;
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
