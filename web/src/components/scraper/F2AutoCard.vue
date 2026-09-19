<script setup lang="ts">
/** 抖音「每日自动获取」设置卡（f2 通道的排期配置）。
 *
 * 与「一键获取素材」同一条链路，区别是**由后端调度循环按间隔自动创建任务**；
 * 到期判定以「最近一次 f2 任务创建时间 + 间隔」为锚（手动跑过一轮也算占用）。
 * 因为本质是「排期」而不是「立刻执行」，这张卡放在「定时采集」页签里。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useF2Import } from '@/composables/useF2Import'
import { formatDate } from '@/utils/format'

const { status, statusLoading, autoSaving, loadStatus, setAuto } = useF2Import()

const autoEnabled = computed(() => status.value?.auto?.enabled ?? false)
const intervalHours = ref(24)

// 状态回读后同步间隔输入框（用户改前端数字时不会被覆盖：仅值不同才写）
watch(
  () => status.value?.auto?.interval_hours,
  (value) => {
    if (value && value !== intervalHours.value) intervalHours.value = value
  },
  { immediate: true },
)

/** 开关：立刻写回后端（含 .env 持久化），失败时回读状态复原开关 */
async function onToggleAuto(value: string | number | boolean) {
  await setAuto(Boolean(value), intervalHours.value)
}

/** 间隔改动：开关处于开启状态时一并生效，关闭时只留作下次开启的默认值 */
async function onIntervalChange(value: number | undefined) {
  if (!value || value === status.value?.auto?.interval_hours) return
  await setAuto(autoEnabled.value, value)
}

/** 「上次运行 / 下次到期」提示：说明自动获取现在的实际行为 */
const autoHint = computed(() => {
  const auto = status.value?.auto
  if (!auto) return ''
  if (!auto.enabled) return '关闭后只有手动点击「一键获取素材 / 采集我的喜欢」时才会获取'
  if (auto.running_task_id) return `已有任务 #${auto.running_task_id} 在执行，本轮不重复触发`
  if (!auto.last_task_at) return '尚无历史任务，调度循环下一轮检查时立即触发'
  const last = `上次 ${formatDate(auto.last_task_at)}`
  return auto.next_due_at ? `${last}，下次 ${formatDate(auto.next_due_at)}` : last
})

onMounted(loadStatus)
</script>

<template>
  <a-card title="抖音每日自动获取（f2 增量下载）" size="small" style="margin-bottom: 16px">
    <a-spin :loading="statusLoading" style="display: block">
      <div class="f2a-row">
        <a-switch
          :model-value="autoEnabled"
          :loading="autoSaving"
          :disabled="!status?.available"
          @change="onToggleAuto"
        />
        <span class="f2a-label">每日自动获取</span>
        <a-input-number
          v-model="intervalHours"
          :min="1"
          :max="720"
          size="small"
          style="width: 110px"
          @change="onIntervalChange"
        />
        <span class="f2a-tip">小时间隔</span>
      </div>

      <div class="f2a-tip f2a-hint">
        {{ autoHint }}
        <template v-if="autoEnabled">
          ；自动获取依赖 f2 的登录 Cookie，Cookie 失效时任务会失败并在任务中心提示，需重新导入
          Cookie。
        </template>
      </div>

      <div class="f2a-tip">
        只自动获取「已登记博主的主页新作品」；「我的喜欢」需要全量翻页，故不参与自动获取，
        请在「采集任务」页签手动触发。
      </div>
    </a-spin>
  </a-card>
</template>

<style scoped>
.f2a-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.f2a-label {
  font-size: 13px;
  color: #1d2129;
}
.f2a-tip {
  font-size: 12px;
  color: #9ca3af;
}
.f2a-hint {
  margin-top: 6px;
  line-height: 1.6;
}
</style>
