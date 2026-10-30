<script setup lang="ts">
/** 姿势选择器：从姿势图库（ai-pose-gallery-zh 只读代理）里挑一个姿势去换。
 *
 * 与「参考图选择器」的区别：参考图是**主体**（人物/服装，可多选），姿势是**要换成的姿态**
 * （单选，只传 id）。选中后由参数面板把它交给提交接口，后端在生成时把姿势图作为
 * **最后一张参考图**上传（提示词约定「第 1 张是主体、最后一张是姿势」）。
 *
 * 数据走本机后端 `/api/pose-gallery`（后端已代理源站并缓存；缩略图用源站直链，
 * `<img>` 展示不需要 CORS）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  fetchPoseGalleryMeta,
  fetchPoses,
  type PoseGalleryMeta,
  type PoseItem,
} from '@/api/poseGallery'

const props = defineProps<{
  visible: boolean
  /** 当前已选姿势 id（打开时高亮；空串=未选） */
  selectedId?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'select', pose: PoseItem): void
}>()

/** 每页张数：40 张 5 列 ≈ 8 行，一屏能看两页的量 */
const PAGE_SIZE = 40

const items = ref<PoseItem[]>([])
const meta = ref<PoseGalleryMeta | null>(null)
const loading = ref(false)
const error = ref('')
const page = ref(1)
const hasMore = ref(false)
const total = ref(0)

const keyword = ref('')
const system = ref('')
const category = ref('')
const angle = ref('')

const systemOptions = computed(() =>
  (meta.value?.systems ?? []).map((f) => ({ label: `${f.name}（${f.count}）`, value: f.name })),
)
const categoryOptions = computed(() =>
  (meta.value?.categories ?? []).map((f) => ({ label: `${f.name}（${f.count}）`, value: f.name })),
)
const angleOptions = computed(() =>
  (meta.value?.angles ?? []).map((f) => ({ label: `${f.name}（${f.count}）`, value: f.name })),
)

/** 打开时：清筛选 + 拉元信息与第一页（筛选不跨次残留）。 */
watch(
  () => props.visible,
  (open) => {
    if (!open) return
    keyword.value = ''
    system.value = ''
    category.value = ''
    angle.value = ''
    void loadMeta()
    void load(1)
  },
)

async function loadMeta(): Promise<void> {
  if (meta.value) return
  try {
    meta.value = await fetchPoseGalleryMeta()
  } catch {
    // 元信息失败不阻塞列表：下拉为空、列表照常按关键字搜
    meta.value = null
  }
}

async function load(target: number): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchPoses({
      q: keyword.value.trim() || undefined,
      system: system.value || undefined,
      category: category.value || undefined,
      angle: angle.value || undefined,
      page: target,
      size: PAGE_SIZE,
    })
    items.value = target === 1 ? data.items : [...items.value, ...data.items]
    total.value = data.total
    page.value = target
    hasMore.value = data.items.length >= PAGE_SIZE && items.value.length < data.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载姿势失败'
    Message.error(error.value)
  } finally {
    loading.value = false
  }
}

/** 关键字回车/点搜索：回到第 1 页。 */
function onSearch(value?: unknown): void {
  if (typeof value === 'string') keyword.value = value
  void load(1)
}

/** 下拉筛选变化：回到第 1 页。 */
function onFilterChange(): void {
  void load(1)
}

function onSelect(pose: PoseItem): void {
  emit('select', pose)
  emit('update:visible', false)
}
</script>

<template>
  <a-modal
    :visible="visible"
    title="选择姿势（换姿势）"
    :width="900"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <div class="pose-toolbar">
      <a-input-search
        v-model="keyword"
        placeholder="搜姿势名称 / 分类 / 编号"
        allow-clear
        style="width: 240px"
        @search="onSearch"
        @press-enter="onSearch"
      />
      <a-select
        v-model="system"
        :options="systemOptions"
        placeholder="全部体系"
        allow-clear
        size="small"
        style="width: 190px"
        @change="onFilterChange"
      />
      <a-select
        v-model="category"
        :options="categoryOptions"
        placeholder="全部分类"
        allow-clear
        size="small"
        style="width: 150px"
        @change="onFilterChange"
      />
      <a-select
        v-model="angle"
        :options="angleOptions"
        placeholder="全部角度"
        allow-clear
        size="small"
        style="width: 130px"
        @change="onFilterChange"
      />
      <span class="pose-hint">共 {{ total }} 个姿势</span>
    </div>
    <div class="pose-note">{{ meta?.note || '图片来自 ai-pose-gallery-zh（个人本地使用）' }}</div>

    <a-spin :loading="loading && !items.length" style="display: block; width: 100%">
      <div v-if="error && !items.length" class="pose-empty">{{ error }}</div>
      <div v-else-if="!items.length && !loading" class="pose-empty">
        没有匹配的姿势，换个关键字或清空筛选试试。
      </div>
      <div v-else class="pose-grid">
        <button
          v-for="pose in items"
          :key="pose.id"
          type="button"
          class="pose-card"
          :class="{ 'is-selected': pose.id === selectedId }"
          :title="`${pose.name} · ${pose.angle}`"
          @click="onSelect(pose)"
        >
          <img :src="pose.thumb_url" :alt="pose.name" loading="lazy" />
          <span class="pose-card-meta">{{ pose.name }}</span>
          <span class="pose-card-sub">{{ pose.category }} · {{ pose.angle }}</span>
        </button>
      </div>
    </a-spin>

    <div v-if="hasMore" class="pose-more">
      <a-button size="small" :loading="loading" @click="load(page + 1)">加载更多</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.pose-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.pose-hint {
  margin-left: auto;
  color: var(--color-text-3);
  font-size: 12px;
}

.pose-note {
  margin-bottom: 10px;
  color: var(--color-text-3);
  font-size: 12px;
}

.pose-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--color-text-3);
  font-size: 13px;
}

.pose-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
  max-height: 62vh;
  overflow-y: auto;
}

.pose-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 6px;
  overflow: hidden;
  background: var(--color-fill-2);
  cursor: pointer;
  text-align: left;
}

.pose-card:hover {
  border-color: rgb(var(--primary-3));
}

.pose-card.is-selected {
  border-color: rgb(var(--primary-6));
}

.pose-card img {
  width: 100%;
  height: 150px;
  object-fit: cover;
  display: block;
  background: #f2f3f5;
}

.pose-card-meta,
.pose-card-sub {
  padding: 0 6px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.pose-card-meta {
  color: var(--color-text-1);
  font-size: 12px;
}

.pose-card-sub {
  margin-bottom: 4px;
  color: var(--color-text-3);
  font-size: 11px;
}

.pose-more {
  margin-top: 10px;
  text-align: center;
}

@media (max-width: 720px) {
  .pose-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
