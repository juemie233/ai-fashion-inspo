<script setup lang="ts">
/** f2 结果浏览与审查面板：本批素材的缩略图网格 + 筛选 + 勾选 + 审查动作。
 *
 * 嵌在「抖音采集历史」卡片内（由该卡片按任务 id 打开）。三种动作与素材库口径一致：
 * 移入垃圾桶（软删除，可还原，同时作为负样本）、还原、彻底删除（不可恢复，
 * 后端创建 batch_delete 任务由 worker 执行）。
 *
 * 图片浏览一律走 common 下的公共组件：DensityImageGrid（自适应列数 + 密度切换）
 * + ThumbCard（内含 HoverImagePreview 悬停大图）+ LoadMoreBar。
 * 自建网格曾因为固定六列 + 网格项最小宽度而把容器撑出横向滚动条，故不再自绘布局。
 */

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { TRASH_REASON_OPTIONS, getFileUrl, type TrashReason } from '@/api/inspirations'
import { openInspiration } from '@/utils/openInspiration'
import { formatDate } from '@/utils/format'
import DensityImageGrid from '@/components/common/DensityImageGrid.vue'
import ThumbCard from '@/components/common/ThumbCard.vue'
import LoadMoreBar from '@/components/common/LoadMoreBar.vue'
import { useF2Results, type F2ResultFilter, type F2ResultItem } from '@/composables/useF2Results'

const props = defineProps<{ taskId: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const router = useRouter()
const reason = ref<TrashReason>('质量差')

/** 网格密度（v-model 给 DensityImageGrid）：与素材库/搜索页一样本地记住选择 */
type DensityMode = 'compact' | 'standard' | 'comfortable'
const DENSITY_STORAGE_KEY = 'f2-results-density'
const density = ref<DensityMode>(
  (localStorage.getItem(DENSITY_STORAGE_KEY) as DensityMode | null) ?? 'standard',
)
watch(density, (value) => localStorage.setItem(DENSITY_STORAGE_KEY, value))

const {
  task,
  batchId,
  items,
  total,
  counts,
  authors,
  filter,
  authorFilter,
  loading,
  acting,
  selectedIds,
  selectedItems,
  selectedLive,
  selectedTrashed,
  open,
  reload,
  setFilter,
  setAuthor,
  loadMore,
  toggleSelect,
  selectAllLoaded,
  trashSelected,
  restoreSelected,
  deleteSelected,
} = useF2Results()

// 打开面板即加载本批结果（任务 id 变化时重新拉取）
watch(
  () => props.taskId,
  (id) => void open(id),
  { immediate: true },
)

const filterOptions: { label: string; value: F2ResultFilter }[] = [
  { label: '全部', value: 'all' },
  { label: '待审核', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
  { label: '垃圾桶', value: 'trash' },
  { label: '已彻底删除', value: 'gone' },
]

/** 列表里所有可操作条目都已勾选 */
function allLoadedSelected(): boolean {
  const selectable = items.value.filter((i) => i.state !== 'gone')
  return selectable.length > 0 && selectable.every((i) => selectedIds.value.has(i.id))
}

/** 打开素材详情页（打标签、深度审核在详情页完成） */
function openDetail(id: string) {
  openInspiration(router, id)
}

/** 悬停预览大图：视频回退首帧缩略图，图片用原图 */
function previewSrc(item: F2ResultItem): string {
  if (item.media_type === 'video') return getFileUrl(item.thumbnail_path || item.file_path || '')
  return getFileUrl(item.file_path || item.thumbnail_path || '')
}

/** 卡片角标：垃圾桶 / 质量审核结论 */
function stateTag(item: F2ResultItem): { text: string; color: string } | null {
  if (item.state === 'trash')
    return { text: `垃圾桶 · ${item.trash_reason || '其他'}`, color: 'orange' }
  if (item.state === 'approved') return { text: '已通过', color: 'green' }
  if (item.state === 'rejected') return { text: '已拒绝', color: 'red' }
  return null
}
</script>

<template>
  <div class="f2-results">
    <div class="f2r-head">
      <div class="f2r-title">
        📋 本批素材
        <span class="f2r-meta">
          共 <b>{{ counts.total }}</b> 条 · 在库 <b>{{ counts.live }}</b> · 垃圾桶
          <b>{{ counts.trash }}</b> · 已彻底删除 <b>{{ counts.gone }}</b>
        </span>
      </div>
      <!-- 右侧信息行用可换行的 flex（a-space 默认不换行，窄屏会把面板撑出横向滚动条） -->
      <div class="f2r-head-actions">
        <span v-if="task" class="f2r-meta">
          任务 #{{ task.id }} · {{ formatDate(task.created_at) }} · 导入 {{ task.imported }} 条
          <template v-if="task.failed">（失败 {{ task.failed }}）</template>
          <template v-if="task.fetch_total">
            · 下载作者 {{ task.fetch_ok }}/{{ task.fetch_total }}
          </template>
        </span>
        <span v-if="batchId" class="f2r-batch">批次 {{ batchId }}</span>
        <a-button size="mini" :loading="loading" @click="reload()">刷新</a-button>
        <a-button size="mini" @click="emit('close')">收起</a-button>
      </div>
    </div>

    <div class="f2r-filter">
      <a-radio-group
        :model-value="filter"
        type="button"
        size="mini"
        @change="(v: unknown) => setFilter(v as F2ResultFilter)"
      >
        <a-radio-button v-for="opt in filterOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </a-radio-button>
      </a-radio-group>
      <a-select
        :model-value="authorFilter"
        :options="[
          { label: '全部博主', value: '' },
          ...authors.map((a) => ({ label: `${a.name}（${a.count}）`, value: a.name })),
        ]"
        size="mini"
        style="width: 160px"
        @change="(v: unknown) => setAuthor((v as string) ?? '')"
      />
    </div>

    <a-spin :loading="loading" style="display: block">
      <div v-if="!items.length && !loading" class="f2r-empty">
        {{
          counts.total
            ? '当前筛选下没有素材（换一个筛选口径看看）'
            : '这批任务没有可浏览的结果（未落批次清单或未导入任何文件）'
        }}
      </div>

      <!-- 图片浏览：网格容器与密度切换、缩略图卡、悬停大图全部走 common 公共组件。
           网格不做内部滚动（自适应列数 + 单元 min-width: 0），从根上避免横向滚动条 -->
      <div v-else>
        <DensityImageGrid v-model:density="density">
          <template #header-left>
            <a-button size="mini" @click="selectAllLoaded">
              {{ allLoadedSelected() ? '取消全选' : '全选已加载' }}
            </a-button>
            <span class="f2r-meta">已选 {{ selectedItems.length }}</span>
            <a-select
              v-model="reason"
              :options="TRASH_REASON_OPTIONS"
              size="mini"
              style="width: 110px"
            />
            <a-popconfirm
              :content="`确定把选中的 ${selectedLive.length} 个素材移入垃圾桶？（可在垃圾桶还原）`"
              :disabled="selectedLive.length === 0"
              @ok="trashSelected(reason)"
            >
              <a-button
                size="mini"
                type="outline"
                status="warning"
                :disabled="!selectedLive.length"
                :loading="acting"
              >
                移入垃圾桶<template v-if="selectedLive.length"
                  >（{{ selectedLive.length }}）</template
                >
              </a-button>
            </a-popconfirm>
            <a-button
              size="mini"
              type="outline"
              status="success"
              :disabled="!selectedTrashed.length"
              :loading="acting"
              @click="restoreSelected"
            >
              还原<template v-if="selectedTrashed.length"
                >（{{ selectedTrashed.length }}）</template
              >
            </a-button>
            <a-popconfirm
              content="彻底删除不可恢复（连同磁盘文件与向量），确定提交批量删除任务？"
              :disabled="selectedItems.length === 0"
              @ok="deleteSelected"
            >
              <a-button
                size="mini"
                type="outline"
                status="danger"
                :disabled="!selectedItems.length"
                :loading="acting"
              >
                彻底删除<template v-if="selectedItems.length"
                  >（{{ selectedItems.length }}）</template
                >
              </a-button>
            </a-popconfirm>
          </template>

          <div
            v-for="item in items"
            :key="item.id"
            class="f2r-cell"
            @click="item.state !== 'gone' && toggleSelect(item.id)"
          >
            <ThumbCard
              v-if="item.state !== 'gone'"
              :src="getFileUrl(item.thumbnail_path || item.file_path || '')"
              :video-src="
                item.media_type === 'video' && !item.thumbnail_path
                  ? getFileUrl(item.file_path || '')
                  : undefined
              "
              :alt="item.caption || item.author || 'f2 素材'"
              :selected="selectedIds.has(item.id)"
              :hover-src="previewSrc(item)"
            >
              <template #extra>
                <div class="f2r-check">
                  <a-checkbox :model-value="selectedIds.has(item.id)" size="small" />
                </div>
                <a-tag
                  v-if="stateTag(item)"
                  class="f2r-tag"
                  size="small"
                  :color="stateTag(item)!.color"
                >
                  {{ stateTag(item)!.text }}
                </a-tag>
                <a-button
                  class="f2r-open"
                  size="mini"
                  type="text"
                  @click.stop="openDetail(item.id)"
                >
                  查看详情
                </a-button>
              </template>
              <template #footer>
                <div class="f2r-caption" :title="item.caption">
                  <b>{{ item.author || '未知博主' }}</b> · {{ item.caption || '（无正文）' }}
                </div>
              </template>
            </ThumbCard>

            <!-- 已被彻底删除：保留占位以说明「本批导入过、现已不存在」 -->
            <div v-else class="f2r-gone">
              <div class="f2r-gone-mark">🗑️ 已彻底删除</div>
              <div class="f2r-gone-meta">{{ item.author || '未知博主' }}</div>
              <div class="f2r-gone-meta" :title="item.caption">
                {{ item.caption || '（无正文）' }}
              </div>
            </div>
          </div>
        </DensityImageGrid>
      </div>

      <LoadMoreBar :loading="loading" :loaded="items.length" :total="total" @load-more="loadMore" />
    </a-spin>
  </div>
</template>

<style scoped>
.f2-results {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fafbfc;
  /* 面板内不允许横向滚动：所有行内元素换行，网格列数由 DensityImageGrid 自适应 */
  overflow-x: hidden;
}
.f2r-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  flex-wrap: wrap;
  min-width: 0;
}
/* 右侧信息行：可换行 + 可收缩（长任务信息/批次号不会把面板撑宽） */
.f2r-head-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.f2r-title {
  font-size: 14px;
  font-weight: 600;
}
.f2r-meta {
  color: #666;
  font-size: 12px;
  font-weight: 400;
  margin-left: 6px;
}
.f2r-batch {
  color: #999;
  font-size: 12px;
  /* 批次号是长串，允许在窄屏下换行，避免把头部撑宽 */
  word-break: break-all;
}
.f2r-filter {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.f2r-empty {
  text-align: center;
  color: #999;
  padding: 32px 0;
  font-size: 13px;
}
/* 网格单元：min-width: 0 允许收缩到轨道宽度以下，图片不会把网格撑宽 */
.f2r-cell {
  min-width: 0;
  cursor: pointer;
}
.f2r-check {
  position: absolute;
  top: 4px;
  right: 4px;
}
.f2r-tag {
  position: absolute;
  top: 4px;
  left: 4px;
  max-width: calc(100% - 40px);
  overflow: hidden;
}
.f2r-open {
  position: absolute;
  bottom: 4px;
  left: 4px;
  opacity: 0;
  transition: opacity 0.15s;
  background: rgba(255, 255, 255, 0.85);
}
.f2r-cell:hover .f2r-open {
  opacity: 1;
}
.f2r-caption {
  padding: 4px 6px;
  font-size: 11px;
  color: #666;
  line-height: 1.35;
  max-height: 32px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-all;
}
.f2r-gone {
  border: 1px dashed #d0d5dd;
  border-radius: 8px;
  background: #f2f4f7;
  min-height: 120px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  text-align: center;
  min-width: 0;
}
.f2r-gone-mark {
  color: #98a2b3;
  font-size: 12px;
  font-weight: 600;
}
.f2r-gone-meta {
  color: #b0b7c3;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
