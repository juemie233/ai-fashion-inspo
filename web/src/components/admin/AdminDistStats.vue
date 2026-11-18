<script setup lang="ts">
/** 分布统计卡片：素材来源、月度新增、分析状态、媒体类型。 */

import type { Stats } from '@/types/admin'
import { sourceLabel } from '@/utils/sourceLabel'

defineProps<{ stats: Stats | null }>()

/** 来源类型颜色 */
function sourceColor(t: string): string {
  const colors: Record<string, string> = {
    xiaohongshu: 'var(--seal)',
    douyin: 'var(--ink)',
    scraper: 'var(--pine)',
    manual_upload: 'var(--pine)',
    browser_extension: 'var(--amber)',
  }
  return colors[t] || 'var(--ink-3)'
}

/** 分析状态颜色 */
function statusColor(s: string): string {
  const colors: Record<string, string> = {
    done: 'var(--pine)',
    error: 'var(--seal)',
    pending: 'var(--ink-3)',
    none: 'var(--ink-3)',
  }
  return colors[s] || 'var(--ink-3)'
}
</script>

<template>
  <div>
    <div class="dist-row">
      <!-- 按来源 -->
      <a-card title="素材来源分布" size="small" style="flex: 1">
        <div v-if="stats?.by_source_type?.length">
          <div v-for="s in stats.by_source_type" :key="s.source_type" class="dist-item">
            <span>{{ sourceLabel(s.source_type) }}</span>
            <span class="dist-bar-wrap">
              <span
                class="dist-bar"
                :style="{
                  width: Math.max((s.count / stats.total_count) * 100, 2) + '%',
                  background: sourceColor(s.source_type),
                }"
              ></span>
            </span>
            <span>{{ s.count }}</span>
          </div>
          <!-- 生成图单独点出来：它会抬高「素材总量」，看采集进度时不该算进去 -->
          <div v-if="stats?.generated_count" class="dist-note">
            其中 AI 生成 <b>{{ stats.generated_count }}</b> 条（{{
              ((stats.generated_count / Math.max(1, stats.total_count)) * 100).toFixed(1)
            }}%）
          </div>
        </div>
        <a-empty v-else description="暂无数据" size="small" />
      </a-card>

      <!-- 按月 -->
      <a-card title="月度新增趋势" size="small" style="flex: 1">
        <div v-if="stats?.by_month?.length">
          <div v-for="m in stats.by_month" :key="m.month" class="dist-item">
            <span>{{ m.month }}</span>
            <span class="dist-bar-wrap">
              <span
                class="dist-bar"
                style="background: var(--pine)"
                :style="{
                  width:
                    Math.max((m.count / Math.max(...stats.by_month.map((x) => x.count))) * 100, 2) +
                    '%',
                }"
              >
              </span>
            </span>
            <span>{{ m.count }}</span>
          </div>
        </div>
        <a-empty v-else description="暂无数据" size="small" />
      </a-card>
    </div>

    <div class="dist-row">
      <!-- 按分析状态 -->
      <a-card title="分析状态分布" size="small" style="flex: 1">
        <div v-if="stats?.by_analysis_status?.length">
          <div v-for="s in stats.by_analysis_status" :key="s.status" class="dist-item">
            <span>{{ s.label }}</span>
            <span class="dist-bar-wrap">
              <span
                class="dist-bar"
                :style="{
                  width: Math.max((s.count / stats.total_count) * 100, 2) + '%',
                  background: statusColor(s.status),
                }"
              >
              </span>
            </span>
            <span>{{ s.count }}</span>
          </div>
        </div>
        <a-empty v-else description="暂无数据" size="small" />
      </a-card>

      <!-- 按媒体类型 -->
      <a-card title="媒体类型分布" size="small" style="flex: 1">
        <div v-if="stats?.by_media_type?.length">
          <div v-for="m in stats.by_media_type" :key="m.media_type" class="dist-item">
            <span>{{
              m.media_type === 'image' ? '图片' : m.media_type === 'video' ? '视频' : m.media_type
            }}</span>
            <span class="dist-bar-wrap">
              <span
                class="dist-bar"
                style="background: var(--pine)"
                :style="{ width: Math.max((m.count / stats.total_count) * 100, 2) + '%' }"
              >
              </span>
            </span>
            <span>{{ m.count }}</span>
          </div>
        </div>
        <a-empty v-else description="暂无数据" size="small" />
      </a-card>
    </div>
  </div>
</template>

<style scoped>
/* 分布统计行 */
.dist-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

/* 分布条 */
.dist-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}
.dist-item span:first-child {
  width: 70px;
  text-align: right;
  flex-shrink: 0;
  color: var(--ink-2);
}
.dist-item span:last-child {
  width: 36px;
  text-align: right;
  flex-shrink: 0;
  font-weight: 600;
}
.dist-bar-wrap {
  flex: 1;
  height: 14px;
  background: var(--sheet-2);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.dist-bar {
  display: block;
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width 0.4s ease;
  min-width: 4px;
}
.dist-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
}
</style>
