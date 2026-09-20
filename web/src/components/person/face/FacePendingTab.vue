<script setup lang="ts">
/** 待审核候选 tab：按人物聚合的候选列表 + 展开明细（勾选/批量确认/驳回/分页/列数）。
 *
 * 从 FaceScanView 模板原样抽出（提取式重构，标记与判定逻辑不变）；数据与动作用
 * props/emit 与视图对接，视图仍是唯一的状态持有者。
 */

import { toRefs } from 'vue'
import type { DetectionItem, PersonAggregateItem } from '@/api/faceScan'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import { isVideoItem, largePreviewUrl, personAvatarUrl, thumbUrl } from '@/utils/faceMedia'

const props = defineProps<{
  /** 待审核候选（按人物聚合） */
  persons: PersonAggregateItem[]
  /** 聚合列表加载态 */
  loading: boolean
  /** 聚合列表当前页 */
  page: number
  /** 聚合列表总数 */
  total: number
  /** 当前展开的人物 key（`type:id`，空串表示未展开） */
  detailKey: string
  /** 展开人物的明细 */
  detailItems: DetectionItem[]
  /** 明细当前页 */
  detailPage: number
  /** 明细总数 */
  detailTotal: number
  /** 明细加载态 */
  detailLoading: boolean
  /** 明细勾选集合 */
  detailChecked: Set<number>
  /** 批量审核进行中 */
  actionBusy: boolean
}>()

const emit = defineEmits<{
  (e: 'toggleDetail', person: PersonAggregateItem): void
  (
    e: 'actOnPerson',
    person: PersonAggregateItem,
    action: 'confirm' | 'reject',
    onlyHigh?: boolean,
  ): void
  (e: 'pageChange', page: number): void
  (e: 'detailPageChange', page: number): void
  (e: 'toggleDetailChecked', id: number, checked: unknown): void
  (e: 'selectAllDetail'): void
  (e: 'actOnChecked', action: 'confirm' | 'reject'): void
  (e: 'openInspiration', inspirationId: string): void
}>()

/** 候选网格列数（3~6 可选，默认 6；原视图状态，经 v-model 双向绑定） */
const gridColumns = defineModel<number>('gridColumns', { required: true })

// 模板与原视图读取同一批名字（props 解构成 ref，模板自动解包）
const {
  persons,
  loading,
  page,
  total,
  detailKey,
  detailItems,
  detailPage,
  detailTotal,
  detailLoading,
  detailChecked,
  actionBusy,
} = toRefs(props)
</script>

<template>
  <a-spin :loading="loading" style="display: block">
    <div v-if="persons.length > 0" class="person-list">
      <div v-for="p in persons" :key="`${p.person_type}:${p.person_id}`" class="person-row">
        <div class="person-head" @click="emit('toggleDetail', p)">
          <!-- 头像用 image-url 传入（而不是插槽 img）：Arco 只在 imageUrl 分支给
               wrapper 加 arco-avatar-image，圆形裁剪（overflow+border-radius）才生效；
               插槽 img 会被渲染成方形并溢出容器 -->
          <a-avatar :size="32" :image-url="personAvatarUrl(p)">
            <template v-if="!personAvatarUrl(p)">{{ p.name.slice(0, 1) }}</template>
          </a-avatar>
          <span class="person-name">{{ p.name }}</span>
          <a-tag size="small" :color="p.person_type === 'blogger' ? 'arcoblue' : 'purple'">
            {{ p.person_type === 'blogger' ? '穿搭博主' : '职业模特' }}
          </a-tag>
          <a-typography-text type="secondary" style="font-size: 12px">
            {{ p.count }} 条候选 · 最高 {{ (p.best_conf ?? 0).toFixed(2) }}
          </a-typography-text>
        </div>
        <a-space :size="6">
          <a-button
            size="mini"
            type="primary"
            :loading="actionBusy"
            @click.stop="emit('actOnPerson', p, 'confirm', true)"
          >
            确认高分（≥0.75）
          </a-button>
          <a-button
            size="mini"
            type="primary"
            :loading="actionBusy"
            @click.stop="emit('actOnPerson', p, 'confirm')"
          >
            全部确认
          </a-button>
          <a-button
            size="mini"
            status="danger"
            :loading="actionBusy"
            @click.stop="emit('actOnPerson', p, 'reject')"
          >
            全部驳回
          </a-button>
        </a-space>

        <!-- 展开明细 -->
        <div v-if="detailKey === `${p.person_type}:${p.person_id}`" class="detail-block">
          <a-spin :loading="detailLoading" style="display: block">
            <div
              v-if="detailItems.length > 0"
              class="detail-grid"
              :style="{ gridTemplateColumns: `repeat(${gridColumns}, 1fr)` }"
            >
              <div
                v-for="item in detailItems"
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
                  :model-value="detailChecked.has(item.detection_id)"
                  @click.stop
                  @change="(v: unknown) => emit('toggleDetailChecked', item.detection_id, v)"
                />
                <span class="detail-conf">{{ (item.confidence ?? 0).toFixed(2) }}</span>
              </div>
            </div>
            <a-empty v-else description="该人物暂无候选明细" size="small" />
          </a-spin>
          <div class="detail-actions">
            <a-space :size="8">
              <a-radio-group v-model="gridColumns" type="button" size="mini">
                <a-radio :value="3">3 列</a-radio>
                <a-radio :value="4">4 列</a-radio>
                <a-radio :value="5">5 列</a-radio>
                <a-radio :value="6">6 列</a-radio>
              </a-radio-group>
              <a-pagination
                v-if="detailTotal > 50"
                size="mini"
                :current="detailPage"
                :page-size="50"
                :total="detailTotal"
                @change="(p: number) => emit('detailPageChange', p)"
              />
            </a-space>
            <a-space :size="6">
              <a-button
                size="mini"
                :disabled="detailItems.length === 0"
                @click="emit('selectAllDetail')"
              >
                {{
                  detailItems.length > 0 &&
                  detailItems.every((i) => detailChecked.has(i.detection_id))
                    ? '取消全选'
                    : '全选'
                }}
              </a-button>
              <a-button
                size="mini"
                type="primary"
                :loading="actionBusy"
                @click="emit('actOnChecked', 'confirm')"
              >
                确认勾选
              </a-button>
              <a-button
                size="mini"
                status="danger"
                :loading="actionBusy"
                @click="emit('actOnChecked', 'reject')"
              >
                驳回勾选
              </a-button>
            </a-space>
          </div>
        </div>
      </div>
    </div>
    <a-empty v-else description="暂无待审核候选，先运行扫描与匹配" />
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
