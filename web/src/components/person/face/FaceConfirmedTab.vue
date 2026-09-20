<script setup lang="ts">
/** 已确认 tab：按人物聚合的已确认人脸 + 展开明细（锁定态，只读）。
 *
 * 从 FaceScanView 模板原样抽出（提取式重构，标记与判定逻辑不变）。
 */

import { toRefs } from 'vue'
import { IconLock } from '@arco-design/web-vue/es/icon'
import type { DetectionItem, PersonAggregateItem } from '@/api/faceScan'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import { isVideoItem, largePreviewUrl, personAvatarUrl, thumbUrl } from '@/utils/faceMedia'

const props = defineProps<{
  /** 已确认关联（按人物聚合） */
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
}>()

const emit = defineEmits<{
  (e: 'toggleDetail', person: PersonAggregateItem): void
  (e: 'pageChange', page: number): void
  (e: 'detailPageChange', page: number): void
  (e: 'openInspiration', inspirationId: string): void
}>()

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
} = toRefs(props)
</script>

<template>
  <a-spin :loading="loading" style="display: block">
    <div v-if="persons.length > 0" class="person-list">
      <div v-for="p in persons" :key="`${p.person_type}:${p.person_id}`" class="person-row">
        <div class="person-head" @click="emit('toggleDetail', p)">
          <a-avatar :size="32" :image-url="personAvatarUrl(p)">
            <template v-if="!personAvatarUrl(p)">{{ p.name.slice(0, 1) }}</template>
          </a-avatar>
          <span class="person-name">{{ p.name }}</span>
          <a-tag size="small" color="green">
            {{ p.person_type === 'blogger' ? '穿搭博主' : '职业模特' }}
          </a-tag>
          <a-typography-text type="secondary" style="font-size: 12px">
            {{ p.count }} 条已确认
          </a-typography-text>
        </div>
        <div v-if="detailKey === `${p.person_type}:${p.person_id}`" class="detail-block">
          <a-spin :loading="detailLoading" style="display: block">
            <div v-if="detailItems.length > 0" class="detail-grid">
              <div
                v-for="item in detailItems"
                :key="item.detection_id"
                class="detail-item locked-item"
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
                <!-- 已确认锁定：锁图标替代勾选框，不可撤销/编辑 -->
                <span class="detail-lock"><IconLock /></span>
                <span class="detail-conf">{{ (item.confidence ?? 0).toFixed(2) }}</span>
              </div>
            </div>
            <a-empty v-else description="该人物暂无已确认明细" size="small" />
          </a-spin>
          <div class="detail-actions">
            <a-pagination
              v-if="detailTotal > 50"
              size="mini"
              :current="detailPage"
              :page-size="50"
              :total="detailTotal"
              @change="(p: number) => emit('detailPageChange', p)"
            />
            <a-typography-text type="secondary" style="font-size: 12px">
              已确认关联已锁定，不可修改或撤销
            </a-typography-text>
          </div>
        </div>
      </div>
    </div>
    <a-empty v-else description="暂无已确认关联" />
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
