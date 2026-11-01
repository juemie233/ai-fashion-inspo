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
  /** 快捷确认门槛（仅影响「确认高分」按钮，不自动确认） */
  highConfidence: number
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
  (e: 'update:highConfidence', value: number): void
  (e: 'openInspiration', inspirationId: string): void
  (e: 'compareFace', item: DetectionItem): void
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
  highConfidence,
} = toRefs(props)
</script>

<template>
  <a-spin :loading="loading" style="display: block">
    <div class="pending-toolbar">
      <a-typography-text type="secondary" style="font-size: 12px">
        AI 候选（已匹配到人物）：仅列出匹配到具体博主的待审记录。驳回会把该人脸标记为「不匹配」，
        <strong>永久退出后续匹配</strong>，请先看脸再操作。
      </a-typography-text>
      <a-space :size="6">
        <span style="font-size: 12px">高分门槛</span>
        <a-input-number
          :model-value="highConfidence"
          :min="0"
          :max="1"
          :step="0.05"
          size="mini"
          style="width: 80px"
          @change="(v: number | undefined) => emit('update:highConfidence', v ?? 0.65)"
        />
      </a-space>
    </div>
    <div v-if="persons.length > 0" class="person-list">
      <div v-for="p in persons" :key="`${p.person_type}:${p.person_id}`" class="person-row">
        <div class="person-head" @click="emit('toggleDetail', p)">
          <!-- 头像用 image-url 传入（而不是插槽 img）：Arco 只在 imageUrl 分支给
               wrapper 加 arco-avatar-image，圆形裁剪（overflow+border-radius）才生效；
               插槽 img 会被渲染成方形并溢出容器 -->
          <a-avatar :size="32" :image-url="personAvatarUrl(p)">
            <template v-if="!personAvatarUrl(p)">{{ p.name.slice(0, 1) }}</template>
          </a-avatar>
          <!-- 名字即入口：新标签页打开该人物详情页（/persons/:id?kind=blogger|model，
               kind 决定详情页按穿搭博主还是职业模特呈现，与 FaceDetectionSection 同口径）。
               @click.stop 挡住行点击，避免「开了新标签，当前页还把明细展开了」 -->
          <a
            class="person-name person-name-link"
            :href="`/persons/${p.person_id}?kind=${p.person_type}`"
            target="_blank"
            rel="noopener noreferrer"
            title="在新标签页查看该人物的详情页"
            @click.stop
            >{{ p.name }}</a
          >
          <a-tag size="small" :color="p.person_type === 'blogger' ? 'arcoblue' : 'purple'">
            {{ p.person_type === 'blogger' ? '穿搭博主' : '职业模特' }}
          </a-tag>
          <a-typography-text type="secondary" style="font-size: 12px">
            {{ p.count }} 条候选 · 平均 {{ (p.avg_conf ?? 0).toFixed(2) }} · 最高
            {{ (p.best_conf ?? 0).toFixed(2) }}
          </a-typography-text>
        </div>
        <a-space :size="6">
          <a-button
            size="mini"
            type="primary"
            :loading="actionBusy"
            @click.stop="emit('actOnPerson', p, 'confirm', true)"
          >
            确认高分（≥{{ highConfidence.toFixed(2) }}）
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
                </HoverImagePreview>
                <a-checkbox
                  class="detail-check"
                  :model-value="detailChecked.has(item.detection_id)"
                  @click.stop
                  @change="(v: unknown) => emit('toggleDetailChecked', item.detection_id, v)"
                />
                <a-button class="compare-btn" size="mini" @click.stop="emit('compareFace', item)">
                  对比
                </a-button>
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
    <a-empty
      v-else
      description="暂无 AI 候选（已匹配到人物）；未匹配人脸请到「未匹配人脸」或「聚合分组」处理"
    />
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

<style scoped>
.compare-btn {
  position: absolute;
  right: 4px;
  bottom: 4px;
  z-index: 2;
}
</style>
