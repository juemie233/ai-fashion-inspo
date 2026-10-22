<script setup lang="ts">
/** 人脸库扫描任务卡：扫描模式 / 自动匹配开关、开始·全库重匹配·取消按钮，以及扫描与匹配
 *  两条任务进度。
 *
 * 从 FaceScanView.vue 抽出（视图只做编排）。本组件不持有任何任务状态：任务对象来自
 * useFaceScanActions 的轮询结果，按钮一律 emit 回抛，视图接到同一批动作上。
 */

import { toRefs } from 'vue'
import type { FaceScanTaskOut } from '@/api/faceScan'
import StatusTag from '@/components/common/StatusTag.vue'

const props = defineProps<{
  /** 有任务在跑：开始 / 重匹配禁用，取消可用 */
  busy: boolean
  /** 人脸子服务离线：离线时禁用扫描与匹配 */
  offline: boolean
  /** 扫描任务（null = 从未跑过） */
  scanTask: FaceScanTaskOut | null
  /** 匹配任务（null = 从未跑过） */
  matchTask: FaceScanTaskOut | null
  /** 开始扫描提交中 */
  starting: boolean
  /** 全库重匹配提交中 */
  matching: boolean
  /** 取消任务提交中 */
  cancelling: boolean
}>()

const emit = defineEmits<{
  (e: 'startScan'): void
  (e: 'startMatch'): void
  (e: 'cancel'): void
}>()

/** 两个开关走 v-model：扫描模式（半增量默认；全量重扫保留锁定记录）与「扫完自动全库匹配」 */
const scope = defineModel<'incremental' | 'semi' | 'all'>('scope', { required: true })
const autoMatch = defineModel<boolean>('autoMatch', { required: true })

// 模板与原视图读取同一批名字（props 解构成 ref，模板自动解包）
const { busy, offline, scanTask, matchTask, starting, matching, cancelling } = toRefs(props)
</script>

<template>
  <a-card size="small" class="task-card" title="人脸库扫描">
    <template #extra>
      <a-space>
        <a-select
          v-model="scope"
          :options="[
            { label: '半增量扫描（跳过已确认素材）', value: 'semi' },
            { label: '增量扫描（仅未扫描素材）', value: 'incremental' },
            { label: '全量重扫（保留已确认记录）', value: 'all' },
          ]"
          size="small"
          style="width: 240px"
        />
        <a-checkbox v-model="autoMatch" size="small">扫完自动全库匹配</a-checkbox>
        <a-button
          size="small"
          type="primary"
          :loading="starting"
          :disabled="busy || offline"
          @click="emit('startScan')"
        >
          开始扫描
        </a-button>
        <a-button
          size="small"
          :loading="matching"
          :disabled="busy || offline"
          @click="emit('startMatch')"
        >
          全库重匹配
        </a-button>
        <a-button
          size="small"
          status="danger"
          :loading="cancelling"
          :disabled="!busy"
          @click="emit('cancel')"
        >
          取消任务
        </a-button>
      </a-space>
    </template>

    <!-- 扫描任务进度 -->
    <div v-if="scanTask" class="task-line">
      <StatusTag :status="scanTask.status" />
      <a-progress
        v-if="['running', 'pending'].includes(scanTask.status)"
        :percent="scanTask.progress / 100"
        size="small"
        style="width: 320px"
      />
      <a-typography-text type="secondary" style="font-size: 12px">
        扫描素材：{{ scanTask.done }} / {{ scanTask.total }}
        <template v-if="scanTask.result?.scanned !== undefined">
          · 检出人脸 {{ scanTask.result.faces }} 张
          <template v-if="scanTask.result.failed_files">
            · 失败 {{ scanTask.result.failed_files }} 个
          </template>
        </template>
      </a-typography-text>
    </div>
    <a-typography-text v-else type="secondary" style="font-size: 12px">
      尚未运行过扫描。首次全量扫描约 10~20 分钟（GPU），增量扫描秒级，可随时取消后续跑。
    </a-typography-text>

    <!-- 匹配任务进度 -->
    <div v-if="matchTask" class="task-line">
      匹配 <StatusTag :status="matchTask.status" />
      <a-progress
        v-if="['running', 'pending'].includes(matchTask.status)"
        :percent="matchTask.progress / 100"
        size="small"
        style="width: 320px"
      />
      <a-typography-text type="secondary" style="font-size: 12px">
        <template v-if="matchTask.result?.matched !== undefined">
          全库比对 {{ matchTask.result.total_faces }} 张人脸 · 命中 {{ matchTask.result.matched }} ·
          未命中 {{ matchTask.result.unmatched }}
          <template v-if="matchTask.result.default_threshold !== undefined">
            · 兜底阈值 {{ matchTask.result.default_threshold }}
            <template v-if="Number(matchTask.result.custom_threshold_count) > 0">
              （另 {{ matchTask.result.custom_threshold_count }} 人按各自阈值判定）
            </template>
          </template>
        </template>
      </a-typography-text>
    </div>
    <a-typography-text
      v-if="scanTask?.error || matchTask?.error"
      type="danger"
      style="font-size: 12px"
    >
      {{ scanTask?.error || matchTask?.error }}
    </a-typography-text>
  </a-card>
</template>

<style scoped>
.task-card {
  margin-bottom: 16px;
}

.task-line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
</style>
