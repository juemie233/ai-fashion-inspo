<script setup lang="ts">
/** 聚合分组 tab：聚类任务状态 + 分组列表（整组指派 / 展开人脸网格 / 勾选指派 / 分页）。
 *
 * 从 FaceScanView 模板原样抽出（提取式重构，标记与判定逻辑不变）；聚类状态由
 * useFaceClusterGroups 持有，视图解构后经 props/emit 传入，子组件不直接碰 composable。
 */

import { toRefs } from 'vue'
import type { DetectionItem, FaceClusterGroup, FaceScanTaskOut } from '@/api/faceScan'
import StatusTag from '@/components/common/StatusTag.vue'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import { groupThumbUrl, isVideoItem, largePreviewUrl, thumbUrl } from '@/utils/faceMedia'

const props = defineProps<{
  /** 聚类任务（未运行时为 null） */
  clusterTask: FaceScanTaskOut | null
  /** 分组列表 */
  clusterGroups: FaceClusterGroup[]
  /** 分组总数 */
  clusterTotal: number
  /** 分组当前页 */
  clusterPage: number
  /** 分组加载态 */
  clusterLoading: boolean
  /** 聚类任务提交中 */
  clustering: boolean
  /** 当前展开的分组 id */
  expandedGroupId: number | null
  /** 展开分组的明细 */
  groupDetailItems: DetectionItem[]
  /** 展开分组明细总数 */
  groupDetailTotal: number
  /** 展开分组明细当前页 */
  groupDetailPage: number
  /** 展开分组明细加载态 */
  groupDetailLoading: boolean
  /** 组内勾选集合 */
  groupChecked: Set<number>
  /** 整组/勾选指派进行中 */
  groupActionBusy: boolean
  /** 人物选择候选（与未匹配 tab 共用） */
  assignOptions: Array<{ label: string; value: number }>
  /** 人物候选加载态 */
  assignLoading: boolean
  /** 是否有任务在运行（决定按钮禁用） */
  busy: boolean
  /** 人物选择器过滤（视图持有，避免两处重复实现） */
  filterOption: (input: string, option: { label?: string }) => boolean
}>()

const emit = defineEmits<{
  (e: 'loadAssignOptions'): void
  (e: 'startCluster'): void
  (e: 'toggleGroupDetail', group: FaceClusterGroup): void
  (e: 'assignGroup', group: FaceClusterGroup): void
  (e: 'loadGroupDetail'): void
  (e: 'loadGroupDetailPage', page: number): void
  (e: 'selectAllGroup', group: FaceClusterGroup): void
  (e: 'clearGroupChecked'): void
  (e: 'assignCheckedGroup'): void
  (e: 'pageChange', page: number): void
  (e: 'toggleGroupChecked', id: number, checked: unknown): void
  (e: 'openInspiration', inspirationId: string): void
}>()

/** 整组指派的人物类型 */
const clusterAssignKind = defineModel<'blogger' | 'model'>('clusterAssignKind', {
  required: true,
})
/** 整组指派的人物 id（未选为 undefined） */
const clusterAssignPersonId = defineModel<number | undefined>('clusterAssignPersonId')

const {
  clusterTask,
  clusterGroups,
  clusterTotal,
  clusterPage,
  clusterLoading,
  clustering,
  expandedGroupId,
  groupDetailItems,
  groupDetailTotal,
  groupDetailPage,
  groupDetailLoading,
  groupChecked,
  groupActionBusy,
  assignOptions,
  assignLoading,
  busy,
} = toRefs(props)
</script>

<template>
  <div class="assign-bar">
    <a-radio-group
      v-model="clusterAssignKind"
      type="button"
      size="small"
      @change="emit('loadAssignOptions')"
    >
      <a-radio value="blogger">穿搭博主</a-radio>
      <a-radio value="model">职业模特</a-radio>
    </a-radio-group>
    <a-select
      v-model="clusterAssignPersonId"
      :options="assignOptions"
      :loading="assignLoading"
      placeholder="选择要指派的人物（整组）"
      size="small"
      style="width: 240px"
      allow-search
      :filter-option="filterOption"
    />
    <a-button
      size="small"
      type="primary"
      :loading="clustering"
      :disabled="busy"
      @click="emit('startCluster')"
    >
      开始聚合聚类
    </a-button>
  </div>

  <a-spin :loading="clusterLoading" style="display: block">
    <!-- 聚类任务状态 -->
    <div v-if="clusterTask" class="task-line">
      聚类 <StatusTag :status="clusterTask.status" />
      <a-progress
        v-if="['running', 'pending'].includes(clusterTask.status)"
        :percent="clusterTask.progress / 100"
        size="small"
        style="width: 320px"
      />
      <a-typography-text type="secondary" style="font-size: 12px">
        <template v-if="clusterTask.result?.total_faces !== undefined">
          共 {{ clusterTask.result.total_faces }} 张未匹配人脸
          <template v-if="clusterTask.result.group_count !== undefined">
            · 聚类出 {{ clusterTask.result.group_count }} 组
          </template>
          <template v-if="clusterTask.result.singletons !== undefined">
            · {{ clusterTask.result.singletons }} 张人脸
          </template>
        </template>
      </a-typography-text>
    </div>
    <a-typography-text v-else type="secondary" style="font-size: 12px">
      尚未运行过聚合聚类。点击「开始聚合聚类」，按相似度以平均链接策略把未匹配人脸分组
      （组间平均相似度达标才合并，防止不同人被链成巨组），便于整组指派给同一位博主/模特。
    </a-typography-text>
    <a-typography-text v-if="clusterTask?.error" type="danger" style="font-size: 12px">
      {{ clusterTask.error }}
    </a-typography-text>

    <!-- 分组列表 -->
    <div v-if="clusterGroups.length > 0" class="person-list" style="margin-top: 12px">
      <div v-for="g in clusterGroups" :key="g.group_id" class="person-row">
        <div class="person-head" @click="emit('toggleGroupDetail', g)">
          <img
            v-if="g.rep_file_path"
            :src="groupThumbUrl(g)"
            class="group-rep-img"
            :alt="`组${g.group_id}`"
          />
          <a-avatar v-else :size="32">{{ g.size }}</a-avatar>
          <span class="person-name">疑似同一人 · {{ g.size }} 张人脸</span>
          <a-typography-text type="secondary" style="font-size: 12px">
            组 #{{ g.group_id }}
          </a-typography-text>
        </div>
        <a-space :size="6">
          <a-button
            size="mini"
            type="primary"
            :loading="groupActionBusy"
            :disabled="!clusterAssignPersonId"
            @click.stop="emit('assignGroup', g)"
          >
            整组指派
          </a-button>
          <a-button size="mini" @click.stop="emit('toggleGroupDetail', g)">
            {{ expandedGroupId === g.group_id ? '收起' : '查看人脸' }}
          </a-button>
        </a-space>

        <!-- 展开：组内人脸网格 + 勾选指派 -->
        <div v-if="expandedGroupId === g.group_id" class="detail-block">
          <a-spin :loading="groupDetailLoading" style="display: block">
            <div v-if="groupDetailItems.length > 0" class="detail-grid">
              <div
                v-for="item in groupDetailItems"
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
                  :model-value="groupChecked.has(item.detection_id)"
                  @click.stop
                  @change="(v: unknown) => emit('toggleGroupChecked', item.detection_id, v)"
                />
                <span v-if="item.confidence !== null" class="detail-conf">
                  {{ item.confidence.toFixed(2) }}
                </span>
              </div>
            </div>
            <a-empty v-else description="该组暂无明细" size="small" />
          </a-spin>
          <div class="detail-actions">
            <a-space :size="8">
              <a-pagination
                v-if="groupDetailTotal > 50"
                size="mini"
                :current="groupDetailPage"
                :page-size="50"
                :total="groupDetailTotal"
                @change="(p: number) => emit('loadGroupDetailPage', p)"
              />
              <a-typography-text type="secondary" style="font-size: 12px">
                已勾选 {{ groupChecked.size }} / {{ g.size }} 张
              </a-typography-text>
            </a-space>
            <a-space :size="6">
              <a-button
                size="mini"
                :disabled="groupDetailLoading"
                @click="emit('selectAllGroup', g)"
              >
                全选
              </a-button>
              <a-button
                size="mini"
                :disabled="groupChecked.size === 0"
                @click="emit('clearGroupChecked')"
              >
                清空
              </a-button>
              <a-button
                size="mini"
                type="primary"
                :loading="groupActionBusy"
                :disabled="groupChecked.size === 0 || !clusterAssignPersonId"
                @click="emit('assignCheckedGroup')"
              >
                指派勾选（{{ groupChecked.size }}）
              </a-button>
            </a-space>
          </div>
        </div>
      </div>
    </div>
    <a-empty
      v-else-if="!clusterLoading && !clusterTask"
      description="暂无聚合分组，先运行「开始聚合聚类」"
    />
    <a-pagination
      v-if="clusterTotal > 20"
      style="margin-top: 12px; justify-content: center"
      :current="clusterPage"
      :page-size="20"
      :total="clusterTotal"
      @change="(p: number) => emit('pageChange', p)"
    />
  </a-spin>
</template>
