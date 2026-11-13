<script setup lang="ts">
/** 素材库批量选择操作栏：收藏 / 移垃圾桶 / 加标签 / 关联博主 / 编辑元数据 / 全选 / 退出。 */

import { computed, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { BatchUpdateFields } from '@/api/inspirations'
import { bloggersApi } from '@/api/persons'
import { SOURCE_TYPE_LABELS } from '@/utils/sourceLabel'

const props = defineProps<{
  /** 已勾选数量 */
  count: number
  /** 当前页是否全选 */
  allSelected: boolean
  /** 已有标签名（加标签时的候选，可按需新建） */
  tagOptions: string[]
}>()

const emit = defineEmits<{
  (e: 'favorite', isFavorite: boolean): void
  (e: 'trash'): void
  (e: 'select-all'): void
  (e: 'add-tags', names: string[]): void
  (e: 'add-bloggers', personIds: number[]): void
  (e: 'add-collection'): void
  (e: 'update', fields: BatchUpdateFields): void
  (e: 'exit'): void
}>()

const sourceOptions = Object.entries(SOURCE_TYPE_LABELS).map(([value, label]) => ({
  label,
  value,
}))
const qualityOptions = [
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]
const aiOptions = [
  { label: '是', value: true },
  { label: '否', value: false },
]

// ── 批量关联穿搭博主 ──
/** 博主选择器选项：按素材数量降序（素材多者在前，后端 sort=count 保证） */
type BloggerOption = { id: number; name: string; inspiration_count?: number }
const bloggerLoading = ref(false)
const bloggerOptions = ref<Array<{ label: string; value: number }>>([])
const bloggerIds = ref<number[]>([])

/**
 * 博主候选懒加载 + 模块级缓存。
 *
 * 旧实现每次进入批量模式都在 onMounted 里串行翻页拉完所有博主（611 个约 4 次
 * 请求），即使这次只想批量收藏/删除；批量选择因此明显变慢。现在：
 * - 只有用户展开「关联穿搭博主」下拉时才发请求；
 * - 先取第一页拿到 total，再并行拉剩余页；
 * - 用 Map 按 id 去重（跨页重叠/分页边界变化时不会出现重复选项）；
 * - 模块级缓存，同一次页面会话内再次进入批量模式不重复拉。
 */
let bloggerOptionsCache: Array<{ label: string; value: number }> | null = null
let bloggerLoadPromise: Promise<Array<{ label: string; value: number }>> | null = null

async function loadBloggerOptions(): Promise<void> {
  if (bloggerOptionsCache) {
    bloggerOptions.value = bloggerOptionsCache
    return
  }
  if (bloggerLoading.value) return
  bloggerLoading.value = true
  try {
    if (!bloggerLoadPromise) {
      bloggerLoadPromise = (async () => {
        const pageSize = 200
        const first = await bloggersApi.fetchList({ sort: 'count', page: 1, size: pageSize })
        const pageCount = Math.ceil(first.total / pageSize)
        const rest = await Promise.all(
          Array.from({ length: Math.max(0, pageCount - 1) }, (_, i) =>
            bloggersApi.fetchList({ sort: 'count', page: i + 2, size: pageSize }),
          ),
        )
        const byId = new Map<number, BloggerOption>()
        for (const page of [first, ...rest]) {
          for (const blogger of page.items) byId.set(blogger.id, blogger)
        }
        return [...byId.values()].map((blogger) => ({
          label: `${blogger.name}（${blogger.inspiration_count ?? 0} 个素材）`,
          value: blogger.id,
        }))
      })()
    }
    bloggerOptionsCache = await bloggerLoadPromise
    bloggerOptions.value = bloggerOptionsCache
  } catch {
    // 加载失败静默：选择器空态展示，不影响批量操作栏其它功能
    bloggerLoadPromise = null
  } finally {
    bloggerLoading.value = false
  }
}

function onBloggerPopupVisible(visible: boolean) {
  if (visible) void loadBloggerOptions()
}

function confirmLinkBloggers() {
  if (bloggerIds.value.length === 0) {
    Message.warning('请选择至少一位穿搭博主')
    return
  }
  emit('add-bloggers', [...bloggerIds.value])
  bloggerIds.value = []
}

// ── 批量评分弹窗 ──
const ratingModalOpen = ref(false)
/** 0 = 未选择/清除；1~5 = 星数（Arco a-rate allow-clear 回 0） */
const ratingValue = ref(0)

function openRatingModal() {
  ratingValue.value = 0
  ratingModalOpen.value = true
}

function applyRating(value: number) {
  emit('update', { rating: value })
  ratingModalOpen.value = false
}

// ── 加标签弹窗 ──
const tagModalOpen = ref(false)
const tagInput = ref<string[]>([])
/** 标签候选按需映射：库内可能有 2 万+ 标签，弹窗关闭时不构造 option 数组，
 * 打开后交给 a-select 虚拟列表只渲染可视项，避免批量栏每次重渲染都映射全量。 */
const tagSelectOptions = computed(() =>
  tagModalOpen.value ? props.tagOptions.map((name) => ({ label: name, value: name })) : [],
)

function openTagModal() {
  tagInput.value = []
  tagModalOpen.value = true
}

function confirmTags() {
  if (tagInput.value.length === 0) {
    Message.warning('请输入至少一个标签')
    return
  }
  emit('add-tags', tagInput.value)
  tagModalOpen.value = false
}

// ── 编辑元数据弹窗 ──
const editModalOpen = ref(false)
// 批量编辑字段值：undefined 表示「不修改」（Arco Select 的 modelValue 类型不含 null，
// 用 undefined 作哨兵值保持 v-model 类型兼容）
const editSource = ref<string>()
const editQuality = ref<'pending' | 'approved' | 'rejected'>()
const editAi = ref<boolean>()

/** a-form 表单模型（Arco 表单要求绑定 model；本弹窗无校验规则，仅提供上下文） */
const editFormModel = reactive({ editSource, editQuality, editAi })

function openEditModal() {
  editSource.value = undefined
  editQuality.value = undefined
  editAi.value = undefined
  editModalOpen.value = true
}

function confirmEdit() {
  const fields: BatchUpdateFields = {}
  if (editSource.value) fields.source_type = editSource.value
  if (editQuality.value) fields.quality_status = editQuality.value
  if (editAi.value !== undefined) fields.is_ai_generated = editAi.value
  if (Object.keys(fields).length === 0) {
    Message.warning('请选择至少一个要修改的字段')
    return
  }
  emit('update', fields)
  editModalOpen.value = false
}
</script>

<template>
  <div class="batch-bar" role="toolbar" aria-label="批量操作">
    <span class="batch-count" aria-live="polite">已选 {{ count }} 个</span>
    <a-button size="mini" @click="emit('favorite', true)">批量收藏</a-button>
    <a-button size="mini" @click="emit('favorite', false)">取消收藏</a-button>
    <a-button size="mini" @click="openTagModal">加标签</a-button>
    <a-button size="mini" @click="openRatingModal">评分</a-button>
    <a-button size="mini" @click="emit('add-collection')">加入合集</a-button>
    <a-select
      v-model="bloggerIds"
      multiple
      filterable
      clearable
      size="mini"
      style="width: 220px"
      placeholder="关联穿搭博主"
      :options="bloggerOptions"
      :loading="bloggerLoading"
      :virtual-list-props="{ height: 240 }"
      @popup-visible-change="onBloggerPopupVisible"
    />
    <a-button
      size="mini"
      type="primary"
      :disabled="bloggerIds.length === 0"
      @click="confirmLinkBloggers"
    >
      关联博主
    </a-button>
    <a-button size="mini" @click="openEditModal">编辑元数据</a-button>
    <a-popconfirm :content="`将所选 ${count} 个素材移入垃圾桶？保留期内可恢复`" @ok="emit('trash')">
      <a-button size="mini" type="secondary" status="danger">移入垃圾桶</a-button>
    </a-popconfirm>
    <a-button size="mini" @click="emit('select-all')">
      {{ allSelected ? '取消全选' : '全选本页' }}
    </a-button>
    <div style="flex: 1" />
    <a-button size="mini" type="text" @click="emit('exit')">退出批量</a-button>
  </div>

  <!-- 批量评分弹窗 -->
  <a-modal v-model:visible="ratingModalOpen" title="批量评分" :width="420">
    <p style="color: #999; font-size: 12px">
      为所选 {{ count }} 个素材设置同一评分；0 星表示清除评分，可再次修改。
    </p>
    <div style="display: flex; align-items: center; gap: 12px">
      <a-rate v-model="ratingValue" allow-clear :count="5" />
      <span style="color: #666; font-size: 12px">
        {{ ratingValue > 0 ? `已选 ${ratingValue} 星` : '未选择（应用即清除评分）' }}
      </span>
    </div>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button size="small" @click="ratingModalOpen = false">取消</a-button>
        <a-button size="small" @click="applyRating(0)">清除评分</a-button>
        <a-button size="small" type="primary" @click="applyRating(ratingValue)"> 确定 </a-button>
      </div>
    </template>
  </a-modal>

  <!-- 加标签弹窗 -->
  <a-modal v-model:visible="tagModalOpen" title="批量添加标签" :width="460">
    <p style="color: #999; font-size: 12px">
      为所选 {{ count }} 个素材批量关联标签（已关联的自动跳过）。
    </p>
    <a-select
      v-model="tagInput"
      multiple
      filterable
      allow-create
      clearable
      placeholder="输入标签名，回车新建；也可从已有标签中选择"
      :options="tagSelectOptions"
      :virtual-list-props="{ height: 240 }"
    />
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button size="small" @click="tagModalOpen = false">取消</a-button>
        <a-button size="small" type="primary" @click="confirmTags">确定</a-button>
      </div>
    </template>
  </a-modal>

  <!-- 编辑元数据弹窗 -->
  <a-modal v-model:visible="editModalOpen" title="批量编辑元数据" :width="460">
    <p style="color: #999; font-size: 12px">
      仅更新所选 {{ count }} 个素材中你显式填写的字段，其余保持不变。
    </p>
    <a-form
      :model="editFormModel"
      label-align="left"
      :label-col-style="{ width: '80px' }"
      size="small"
    >
      <a-form-item label="来源">
        <a-select v-model="editSource" clearable placeholder="不修改" :options="sourceOptions" />
      </a-form-item>
      <a-form-item label="审核状态">
        <a-select v-model="editQuality" clearable placeholder="不修改" :options="qualityOptions" />
      </a-form-item>
      <a-form-item label="疑似 AI">
        <a-select v-model="editAi" clearable placeholder="不修改" :options="aiOptions" />
      </a-form-item>
    </a-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button size="small" @click="editModalOpen = false">取消</a-button>
        <a-button size="small" type="primary" @click="confirmEdit">确定</a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #eef4ff;
  border: 1px solid #c8d8f5;
  border-radius: 8px;
}
.batch-count {
  font-size: 13px;
  color: #2d5b9a;
  font-weight: 500;
}
</style>
