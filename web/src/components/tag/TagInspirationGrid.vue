<script setup lang="ts">
/** 标签素材网格：展示某标签关联的素材，支持跳转详情、悬停快捷操作、多选批量操作。
 *  网格交互（灯箱/多选/密度/加载更多）复用通用组件 InspirationGridBrowser，
 *  本组件只负责数据加载与「移除标签 / 批量移除 / 批量添加标签」等标签域操作。 */

import { ref, watch, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import {
  fetchTagInspirations,
  batchRemoveTagInspirations,
  type TagInspiration,
  type TagItem,
} from '@/api/tags'
import { removeTagFromInspiration, batchAddTagsToInspirations } from '@/api/inspirations'
import InspirationGridBrowser, {
  type GridBrowserItem,
} from '@/components/inspiration/InspirationGridBrowser.vue'

const props = defineProps<{
  /** 当前选中的标签 */
  tag: TagItem | null
}>()

const emit = defineEmits<{
  /** 素材关联数发生变化（单个移除=1，批量移除=N），供父组件同步 usage_count/统计 */
  (e: 'changed', payload: { removed: number }): void
}>()

const router = useRouter()

const gridBrowserRef = ref<InstanceType<typeof InspirationGridBrowser> | null>(null)

// ===== 列表数据 =====
const items = ref<TagInspiration[]>([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const sort = ref<'newest' | 'oldest' | 'confidence'>((localStorage.getItem('tag-grid-sort') as 'newest' | 'oldest' | 'confidence') || 'newest')
const density = ref<'compact' | 'standard'>((localStorage.getItem('tag-grid-density') as 'compact' | 'standard') || 'compact')

const sortOptions = [
  { label: '最新', value: 'newest' },
  { label: '最旧', value: 'oldest' },
  { label: '置信度', value: 'confidence' },
]

/** 映射为通用网格条目（id 取 inspiration_id） */
const gridItems = computed<GridBrowserItem[]>(() =>
  items.value.map((i) => ({ ...i, id: i.inspiration_id })),
)

// 持久化排序与密度：刷新或再次进入时保持上次选择
watch(sort, (v) => { localStorage.setItem('tag-grid-sort', v) })
watch(density, (v) => { localStorage.setItem('tag-grid-density', v) })

// ===== 批量添加标签 =====
const showBatchAddModal = ref(false)
const batchAddNames = ref('')
const batchAddCategory = ref('free')
const batchAdding = ref(false)
/** 打开批量添加弹窗时记录的选中 id（弹窗内确定后使用） */
const batchActionIds = ref<string[]>([])

// ===== 单个移除中 / 批量移除中 =====
const removingIds = ref<Set<string>>(new Set())
const batchRemoving = ref(false)

/** 切换标签时重置状态并重新加载 */
watch(
  () => props.tag?.id,
  () => {
    page.value = 1
    load(true)
  },
  { immediate: true },
)

/** 加载代际号：切换标签/排序/加载更多时自增，防止过期响应覆盖新数据 */
let loadSeq = 0

async function load(reset = true) {
  if (!props.tag) return
  if (reset) page.value = 1
  const tagId = props.tag.id
  const seq = ++loadSeq
  loading.value = true
  try {
    const data = await fetchTagInspirations(tagId, page.value, 50, sort.value)
    // 竞态防护：请求在途时标签/排序已切换，丢弃过期结果（避免旧标签数据串入新列表）
    if (seq !== loadSeq || props.tag?.id !== tagId) return
    items.value = reset ? data.items : [...items.value, ...data.items]
    total.value = data.total
  } catch {
    if (seq !== loadSeq) return
    Message.error('加载素材失败')
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function loadMore() {
  page.value += 1
  load(false)
}

/** 单击缩略图跳转详情页 */
function openDetail(item: GridBrowserItem) {
  router.push({ name: 'detail', params: { id: item.id } })
}

/** 悬停快捷操作——移除该标签 */
async function removeOne(item: GridBrowserItem) {
  if (!props.tag || removingIds.value.has(item.id)) return
  removingIds.value = new Set(removingIds.value).add(item.id)
  try {
    await removeTagFromInspiration(item.id, props.tag.id)
    items.value = items.value.filter((i) => i.inspiration_id !== item.id)
    total.value = Math.max(0, total.value - 1)
    gridBrowserRef.value?.removeSelectedId(item.id)  // 同步清除选中残留
    emit('changed', { removed: 1 })
    Message.success('已移除该标签')
  } catch {
    Message.error('移除失败')
  } finally {
    const next = new Set(removingIds.value)
    next.delete(item.id)
    removingIds.value = next
  }
}

/** 批量移除标签（选中集来自通用组件 slot） */
async function batchRemove(ids: string[], clear: () => void) {
  if (!props.tag || ids.length === 0) return
  batchRemoving.value = true
  try {
    const { removed } = await batchRemoveTagInspirations(props.tag.id, ids)
    Message.success(`已从 ${removed} 个素材移除该标签`)
    clear()
    emit('changed', { removed })
    await load(true)
  } catch {
    Message.error('批量移除失败')
  } finally {
    batchRemoving.value = false
  }
}

/** 打开批量添加弹窗：记录当前选中 id */
function openBatchAdd(ids: string[]) {
  batchActionIds.value = ids
  showBatchAddModal.value = true
}

/** 批量给选中素材添加标签（按名称查找或创建） */
async function batchAddTags() {
  if (batchActionIds.value.length === 0 || !batchAddNames.value.trim()) return
  // 支持逗号/顿号/空格分隔多个标签名
  const names = batchAddNames.value
    .split(/[,，、\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (names.length === 0) {
    Message.warning('请输入标签名')
    return
  }
  batchAdding.value = true
  try {
    const { added, affected, not_found, skipped_existing } = await batchAddTagsToInspirations(
      batchActionIds.value,
      names,
      batchAddCategory.value,
    )
    // 明细提示：区分「实际新增」「素材不存在」「关联已存在」，避免误以为全部成功
    const parts = [`已为 ${affected} 个素材添加 ${added} 个标签`]
    if (not_found > 0) parts.push(`${not_found} 个素材不存在`)
    if (skipped_existing > 0) parts.push(`${skipped_existing} 条关联已存在`)
    Message.success(parts.join('，'))
    showBatchAddModal.value = false
    batchAddNames.value = ''
    // 添加标签不影响当前标签的关联数，但标签 usage 可能变化，通知父组件刷新统计
    emit('changed', { removed: 0 })
  } catch {
    Message.error('批量添加标签失败')
  } finally {
    batchAdding.value = false
  }
}
</script>

<template>
  <template v-if="tag">
    <InspirationGridBrowser
      ref="gridBrowserRef"
      :items="gridItems"
      :total="total"
      :loading="loading"
      v-model:density="density"
      v-model:sort="sort"
      :sort-options="sortOptions"
      show-sort
      empty-text="暂无素材"
      @load-more="loadMore"
      @open-detail="openDetail"
    >
      <!-- 头部：标签名 + 使用次数 -->
      <template #header-left>
        <h3 style="margin:0">「{{ tag.name }}」</h3>
        <a-tag size="small" style="margin-left:8px">{{ tag.usage_count }} 次</a-tag>
        <span style="font-size:13px;color:#999;margin-left:8px">共 {{ total }} 个</span>
      </template>

      <!-- 批量操作栏：全选 + 批量移除/批量添加 -->
      <template #batch-actions="{ ids, count, clear, allSelected, toggleAll }">
        <a-checkbox :model-value="allSelected" :indeterminate="count > 0 && !allSelected" @change="toggleAll" />
        <span style="font-size:13px">已选 {{ count }} 个</span>
        <a-popconfirm
          :content="`确认批量移除 ${count} 个关联？此操作不可恢复`"
          @ok="batchRemove(ids, clear)"
        >
          <a-button size="mini" type="secondary" status="danger" :loading="batchRemoving">
            批量移除该标签
          </a-button>
        </a-popconfirm>
        <a-button size="mini" type="secondary" @click="openBatchAdd(ids)">
          批量添加标签
        </a-button>
        <a-button size="mini" @click="clear">取消选择</a-button>
      </template>

      <!-- 卡片悬停操作：移除该标签（大图按钮由通用组件内置） -->
      <template #card-actions="{ item }">
        <a-popconfirm
          content="确认移除该素材关联？"
          @ok="removeOne(item)"
        >
          <a-button
            size="mini"
            type="outline"
            status="danger"
            :loading="removingIds.has(item.id)"
          >移除</a-button>
        </a-popconfirm>
      </template>
    </InspirationGridBrowser>

    <!-- 批量添加标签弹窗 -->
    <a-modal v-model:visible="showBatchAddModal" title="批量添加标签" :footer="false" :width="480">
      <p style="font-size:13px;color:#999;margin:0 0 12px">
        将为选中的 {{ batchActionIds.length }} 个素材添加以下标签（已存在的关联自动跳过）：
      </p>
      <a-form :model="{ batchAddNames, batchAddCategory }" label-align="left" :label-col-style="{ width: '60px' }" size="small">
        <a-form-item label="标签名">
          <a-textarea
            v-model="batchAddNames"
            :auto-size="{ minRows: 3 }"
            placeholder="多个标签用逗号/顿号分隔，例如：御姐风, 长腿, 高跟鞋"
          />
        </a-form-item>
        <a-form-item label="类别">
          <a-select
            v-model="batchAddCategory"
            :options="[
              { label: '风格', value: 'style' },
              { label: '单品', value: 'item_type' },
              { label: '颜色', value: 'color' },
              { label: '穿着方式', value: 'body_part' },
              { label: '版型', value: 'fit' },
              { label: '属性', value: 'attribute' },
              { label: '自定义', value: 'free' },
              { label: '穿搭大标签', value: 'outfit' },
            ]"
          />
        </a-form-item>
      </a-form>
      <a-space style="display:flex;justify-content:flex-end;margin-top:16px">
        <a-button @click="showBatchAddModal = false">取消</a-button>
        <a-button type="primary" :loading="batchAdding" :disabled="!batchAddNames.trim()" @click="batchAddTags">确认添加</a-button>
      </a-space>
    </a-modal>
  </template>

  <div v-else class="grid-placeholder">点击左侧标签查看关联素材</div>
</template>

<style scoped>
.grid-placeholder {
  color: #999;
  text-align: center;
  padding: 60px 20px;
  font-size: 14px;
}
</style>
