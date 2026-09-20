<script setup lang="ts">
/** 未匹配人脸 tab：指派栏（人物选择）+ 人脸网格 + 勾选批量指派 + 分页。
 *
 * 从 FaceScanView 模板原样抽出（提取式重构，标记与判定逻辑不变）。
 */

import { toRefs } from 'vue'
import type { DetectionItem } from '@/api/faceScan'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import { isVideoItem, largePreviewUrl, thumbUrl } from '@/utils/faceMedia'

const props = defineProps<{
  /** 未匹配人脸列表 */
  items: DetectionItem[]
  /** 列表加载态 */
  loading: boolean
  /** 列表当前页 */
  page: number
  /** 列表总数 */
  total: number
  /** 勾选集合 */
  checked: Set<number>
  /** 人物选择候选 */
  assignOptions: Array<{ label: string; value: number }>
  /** 人物候选加载态 */
  assignLoading: boolean
  /** 指派进行中 */
  assigning: boolean
  /** 人物选择器过滤（视图持有，避免两处重复实现） */
  filterOption: (input: string, option: { label?: string }) => boolean
}>()

const emit = defineEmits<{
  (e: 'loadAssignOptions'): void
  (e: 'assign'): void
  (e: 'pageChange', page: number): void
  (e: 'toggleChecked', id: number, checked: unknown): void
  (e: 'openInspiration', inspirationId: string): void
}>()

/** 指派目标人物类型（博主/模特） */
const assignKind = defineModel<'blogger' | 'model'>('assignKind', { required: true })
/** 指派目标人物 id（未选为 undefined） */
const assignPersonId = defineModel<number | undefined>('assignPersonId')

const { items, loading, page, total, checked, assignOptions, assignLoading, assigning } =
  toRefs(props)
</script>

<template>
  <div class="assign-bar">
    <a-radio-group
      v-model="assignKind"
      type="button"
      size="small"
      @change="emit('loadAssignOptions')"
    >
      <a-radio value="blogger">穿搭博主</a-radio>
      <a-radio value="model">职业模特</a-radio>
    </a-radio-group>
    <a-select
      v-model="assignPersonId"
      :options="assignOptions"
      :loading="assignLoading"
      placeholder="选择要指派的人物"
      size="small"
      style="width: 240px"
      allow-search
      :filter-option="filterOption"
    />
    <a-button size="small" type="primary" :loading="assigning" @click="emit('assign')">
      指派勾选（{{ checked.size }}）
    </a-button>
  </div>
  <a-spin :loading="loading" style="display: block">
    <div v-if="items.length > 0" class="detail-grid unmatched-grid">
      <div
        v-for="item in items"
        :key="item.detection_id"
        class="detail-item"
        @click="emit('openInspiration', item.inspiration_id)"
      >
        <HoverImagePreview class="image-wrap" :large-src="largePreviewUrl(item)">
          <img :src="thumbUrl(item)" loading="lazy" />
          <!-- 视频素材角标：缩略图可能无动态大图，提示可跳详情页播放 -->
          <span
            v-if="isVideoItem(item)"
            class="face-video-badge"
            title="视频素材，点击查看详情后播放"
            >▶</span
          >
        </HoverImagePreview>
        <a-checkbox
          class="detail-check"
          :model-value="checked.has(item.detection_id)"
          @click.stop
          @change="(v: unknown) => emit('toggleChecked', item.detection_id, v)"
        />
      </div>
    </div>
    <a-empty v-else description="暂无未匹配人脸" />
    <a-pagination
      v-if="total > 50"
      style="margin-top: 12px; justify-content: center"
      :current="page"
      :page-size="50"
      :total="total"
      @change="(p: number) => emit('pageChange', p)"
    />
  </a-spin>
</template>
