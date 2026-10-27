<script setup lang="ts">
/** 人物素材区（B1 作品视图 + B2 筛选）：详情页最重的一块，从视图抽出。
 *
 * 为什么不留在 `PersonDetailView`：素材区同时管「两种视图 + 六类筛选 + 分页 + 人脸
 * 注销多选」，再留在视图里会把编排层撑到不可读（项目约定视图只做编排）。
 *
 * 两种视图的分工：
 * - **全部（瀑布流）**：按张看，适合素材量 1~99 的博主（全库 475/498 位）；
 * - **作品**：按「一次发布」分组（同一抖音作品的 6~9 张图一组），适合 500+ 的头部博主
 *   （9 位，最大 3,426 条）——逐张刷屏翻不动。
 */

import { computed } from 'vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import type { InspirationOut } from '@/api/inspirations'
import type { PersonInspiration, PersonInspirationFilters, PersonWorkGroup } from '@/api/persons'
import { getFileUrl } from '@/api/inspirations'
import type { PersonType } from '@shared/types/person'

const props = defineProps<{
  kind: PersonType
  items: PersonInspiration[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  filters: PersonInspirationFilters
  sort: string
  viewMode: 'list' | 'works'
  works: PersonWorkGroup[]
  worksTotal: number
  worksTotalItems: number
  worksPage: number
  worksPageSize: number
  worksLoading: boolean
  /** 该人物用过的高频标签（筛选下拉用；来自风格画像，避免多发一次请求） */
  tagOptions: string[]
  unbindMode: boolean
  unbindSelectedIds: Set<string>
  unbindSubmitting: boolean
  clearingAll: boolean
  isCurrentPageAllSelected: boolean
  /** 当前页是否有图片素材（决定「全屏浏览」能否点：全是视频时打开灯箱只会空白） */
  hasImageItems: boolean
}>()

const emit = defineEmits<{
  (e: 'update:filters', value: PersonInspirationFilters): void
  (e: 'update:sort', value: string): void
  (e: 'update:view-mode', value: 'list' | 'works'): void
  (e: 'page-change', page: number): void
  (e: 'works-page-change', page: number): void
  (e: 'open-lightbox'): void
  /** 随机浏览：由视图打开随机浏览弹窗（用的是同一份筛选条件） */
  (e: 'random-browse'): void
  (e: 'open-work', paths: string[]): void
  (e: 'enter-unbind'): void
  (e: 'exit-unbind'): void
  (e: 'toggle-select', id: string): void
  (e: 'toggle-select-page'): void
  (e: 'confirm-unbind'): void
  (e: 'unbind-all'): void
  (e: 'clear-filters'): void
}>()

const sortOptions = [
  { label: '最新入库', value: 'newest' },
  { label: '最早入库', value: 'oldest' },
  { label: '按匹配置信度', value: 'confidence' },
]

const mediaOptions = [
  { label: '全部类型', value: '' },
  { label: '图片', value: 'image' },
  { label: '视频', value: 'video' },
]

const qualityOptions = [
  { label: '全部质量', value: '' },
  { label: '通过', value: 'approved' },
  { label: '待审', value: 'pending' },
  { label: '拒绝', value: 'rejected' },
]

const ratingOptions = [
  { label: '全部评分', value: 0 },
  { label: '≥ 3 星', value: 3 },
  { label: '≥ 4 星', value: 4 },
  { label: '5 星', value: 5 },
]

/** 素材 → MasonryGrid 需要的结构 */
function toInspirationOut(item: PersonInspiration): InspirationOut {
  return {
    id: item.inspiration_id,
    file_path: item.file_path,
    thumbnail_path: item.thumbnail_path,
    media_type: item.media_type,
    is_favorite: Boolean(item.is_favorite),
    created_at: item.created_at || '',
    tags: [],
    analysis_status: 'none',
  }
}

const gridItems = computed(() => props.items.map(toInspirationOut))

/** 是否处于「筛选生效」状态（决定要不要显示「清空筛选」） */
const filtered = computed(() =>
  Boolean(
    props.filters.media_type ||
    props.filters.quality_status ||
    props.filters.tag_name ||
    props.filters.rating_min ||
    props.filters.favorite ||
    props.filters.face_matched,
  ),
)

function patchFilters(patch: Partial<PersonInspirationFilters>): void {
  emit('update:filters', { ...props.filters, ...patch })
}

/** 作品卡内缩略图（最多 6 张，避免一屏塞满） */
function workThumbs(work: PersonWorkGroup): string[] {
  return work.items
    .slice(0, 6)
    .map((i) => i.thumbnail_path || i.file_path)
    .filter((p): p is string => Boolean(p))
    .map((p) => getFileUrl(p))
}

/** 打开某个作品：把该组素材路径交给灯箱（与全局「全屏浏览」共用同一个灯箱） */
function openWork(work: PersonWorkGroup): void {
  const paths = work.items.map((i) => i.file_path).filter((p): p is string => Boolean(p))
  if (paths.length) emit('open-work', paths)
}
</script>

<template>
  <a-card size="small" class="items-card">
    <!-- 人脸注销多选模式工具栏（仅博主） -->
    <a-alert v-if="kind === 'blogger' && unbindMode" type="warning" style="margin-bottom: 12px">
      <template #title>
        <div class="unbind-toolbar">
          <span>
            人脸注销模式：勾选要注销的素材，确定后将同时解除其归属绑定、回退识别为该博主的人脸记录（人脸特征保留，不可撤销）
          </span>
          <a-space>
            <a-button size="small" @click="emit('toggle-select-page')">
              {{ isCurrentPageAllSelected ? '取消全选本页' : '全选本页' }}
            </a-button>
            <a-button size="small" @click="emit('exit-unbind')">取消</a-button>
            <a-popconfirm
              type="warning"
              :ok-loading="unbindSubmitting"
              ok-text="确认注销"
              :ok-button-props="{ status: 'warning' }"
              :content="`确定注销选中的 ${unbindSelectedIds.size} 个素材？将解除它们与该博主的归属绑定，并把识别为该博主的人脸记录回退为未匹配（人脸特征保留）。`"
              @ok="emit('confirm-unbind')"
            >
              <a-button
                size="small"
                type="primary"
                status="warning"
                :loading="unbindSubmitting"
                :disabled="unbindSelectedIds.size === 0"
              >
                确定注销（{{ unbindSelectedIds.size }}）
              </a-button>
            </a-popconfirm>
          </a-space>
        </div>
      </template>
    </a-alert>

    <div class="items-header">
      <h3 style="margin: 0">
        TA 的素材
        <span class="hint">
          {{
            viewMode === 'works' ? `${worksTotal} 个作品 / ${worksTotalItems} 条` : `${total} 条`
          }}
        </span>
      </h3>
      <a-space>
        <a-radio-group
          :model-value="viewMode"
          size="small"
          type="button"
          @update:model-value="emit('update:view-mode', $event as 'list' | 'works')"
        >
          <a-radio value="list">全部</a-radio>
          <a-radio value="works">作品</a-radio>
        </a-radio-group>
        <a-button
          size="small"
          secondary
          :disabled="unbindMode || !hasImageItems"
          :title="hasImageItems ? '' : '当前页没有图片素材（视频不进图片灯箱）'"
          @click="emit('open-lightbox')"
        >
          🖼️ 全屏浏览
        </a-button>
        <!-- 随机浏览：素材 500+ 的博主靠这个才逛得动（与筛选条件共用） -->
        <a-button
          size="small"
          secondary
          :disabled="unbindMode || total === 0"
          :title="total === 0 ? '该人物还没有素材' : '从当前筛选结果里随机抽一张来逛'"
          @click="emit('random-browse')"
        >
          🎲 随机浏览
        </a-button>
        <a-button
          v-if="kind === 'blogger' && !unbindMode"
          size="small"
          status="warning"
          :disabled="total === 0"
          @click="emit('enter-unbind')"
        >
          人脸注销
        </a-button>
        <a-popconfirm
          v-if="kind === 'blogger' && !unbindMode"
          type="warning"
          :ok-loading="clearingAll"
          ok-text="确认清空"
          :ok-button-props="{ status: 'warning' }"
          @ok="emit('unbind-all')"
        >
          <template #content>
            <div style="max-width: 300px">
              将解除该博主与<b>全部 {{ total }} 个素材</b>的归属绑定，并把这些素材里识别为
              该博主的人脸记录回退为「未匹配」（人脸扫描审核页可重新看到它们）。
              <b>人脸特征会保留</b>，以后仍可自动匹配。此操作不可撤销。
            </div>
          </template>
          <a-button size="small" status="warning" :loading="clearingAll" :disabled="total === 0">
            清空全部绑定
          </a-button>
        </a-popconfirm>
      </a-space>
    </div>

    <!-- 筛选工具条：只在「全部」视图生效（作品视图按作品粒度翻，不做逐张筛选） -->
    <div v-if="viewMode === 'list'" class="items-filters">
      <a-select
        :model-value="sort"
        :options="sortOptions"
        size="small"
        style="width: 132px"
        @update:model-value="emit('update:sort', String($event))"
      />
      <a-select
        :model-value="filters.media_type || ''"
        :options="mediaOptions"
        size="small"
        style="width: 110px"
        @update:model-value="
          patchFilters({ media_type: ($event as '' | 'image' | 'video') || undefined })
        "
      />
      <a-select
        :model-value="filters.quality_status || ''"
        :options="qualityOptions"
        size="small"
        style="width: 110px"
        @update:model-value="
          patchFilters({
            quality_status: ($event as '' | 'approved' | 'pending' | 'rejected') || undefined,
          })
        "
      />
      <a-select
        v-if="tagOptions.length"
        :model-value="filters.tag_name || ''"
        :options="[
          { label: '全部标签', value: '' },
          ...tagOptions.map((t) => ({ label: t, value: t })),
        ]"
        size="small"
        style="width: 130px"
        @update:model-value="patchFilters({ tag_name: ($event as string) || undefined })"
      />
      <a-select
        :model-value="filters.rating_min || 0"
        :options="ratingOptions"
        size="small"
        style="width: 110px"
        @update:model-value="patchFilters({ rating_min: ($event as number) || undefined })"
      />
      <a-checkbox
        :model-value="Boolean(filters.favorite)"
        @update:model-value="patchFilters({ favorite: Boolean($event) })"
      >
        已收藏
      </a-checkbox>
      <a-checkbox
        :model-value="Boolean(filters.face_matched)"
        @update:model-value="patchFilters({ face_matched: Boolean($event) })"
      >
        人脸已匹配
      </a-checkbox>
      <a-button v-if="filtered" size="mini" type="text" @click="emit('clear-filters')">
        清空筛选
      </a-button>
    </div>

    <!-- 视图一：全部（瀑布流） -->
    <template v-if="viewMode === 'list'">
      <MasonryGrid
        :items="gridItems"
        :loading="loading"
        :show-actions="false"
        :selectable="kind === 'blogger' && unbindMode"
        :selected-ids="unbindSelectedIds"
        :show-view-button="unbindMode"
        :empty-text="
          filtered ? '没有符合筛选条件的素材' : '该人物还没有素材，去素材详情页关联或按博主采集吧'
        "
        @toggle-select="(id: string) => emit('toggle-select', id)"
      />
      <a-pagination
        v-if="total > pageSize"
        style="margin-top: 16px; justify-content: flex-end"
        :current="page"
        :page-size="pageSize"
        :total="total"
        @change="(p: number) => emit('page-change', p)"
      />
    </template>

    <!-- 视图二：作品（按发布分组） -->
    <template v-else>
      <a-spin :loading="worksLoading" style="display: block">
        <a-alert
          v-if="worksTotal > 0 && worksTotal === worksTotalItems"
          type="info"
          size="small"
          style="margin-bottom: 10px"
        >
          该人物的素材没有作品 ID（多是单图/其他来源），每张各自成组——作品视图与「全部」
          等价，可按「全部」逐张看更直观。
        </a-alert>
        <div v-if="!works.length && !worksLoading" class="items-empty">还没有可分组作品</div>
        <div class="work-grid">
          <div
            v-for="work in works"
            :key="work.work_id || work.cover.inspiration_id"
            class="work-card"
            :title="`${work.count} 张${work.work_id ? '' : '（无作品 ID，单独一组）'}`"
            @click="openWork(work)"
          >
            <img
              :src="getFileUrl(work.cover.thumbnail_path || work.cover.file_path)"
              alt=""
              loading="lazy"
            />
            <span class="work-count">{{ work.count }} 张</span>
            <div v-if="workThumbs(work).length > 1" class="work-thumbs">
              <img
                v-for="src in workThumbs(work).slice(0, 4)"
                :key="src"
                :src="src"
                alt=""
                loading="lazy"
              />
            </div>
          </div>
        </div>
      </a-spin>
      <a-pagination
        v-if="worksTotal > worksPageSize"
        style="margin-top: 16px; justify-content: flex-end"
        :current="worksPage"
        :page-size="worksPageSize"
        :total="worksTotal"
        @change="(p: number) => emit('works-page-change', p)"
      />
    </template>
  </a-card>
</template>

<style scoped>
.items-card {
  margin-bottom: 24px;
}
.items-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.hint {
  color: var(--color-text-3);
  font-size: 12px;
  font-weight: normal;
  margin-left: 6px;
}
.items-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--color-border-2);
}
.items-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--color-text-3);
  font-size: 13px;
}
.work-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.work-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  cursor: zoom-in;
  background: var(--color-fill-2);
}
.work-card > img {
  width: 100%;
  aspect-ratio: 3 / 4;
  object-fit: cover;
  display: block;
}
.work-count {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 0 6px;
  border-radius: 8px;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
}
.work-thumbs {
  position: absolute;
  left: 4px;
  bottom: 4px;
  display: flex;
  gap: 2px;
}
.work-thumbs img {
  width: 22px;
  height: 22px;
  object-fit: cover;
  border-radius: 3px;
  border: 1px solid rgb(255 255 255 / 70%);
}
.unbind-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
  line-height: 1.6;
}
</style>
