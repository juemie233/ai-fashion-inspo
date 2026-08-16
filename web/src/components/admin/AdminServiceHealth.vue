<script setup lang="ts">
/** 服务健康面板：展示后端 / 前端 / worker 状态、资源占用与告警，每 10 秒自动刷新。 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import apiClient from '@/api/client'
import { formatSize } from '@/utils/format'
import type { ServiceHealth } from '@/types/admin'

const health = ref<ServiceHealth | null>(null)
const error = ref('')
let timer: number | null = null

const SERVICE_KEYS = ['backend', 'frontend', 'worker'] as const
type ServiceKey = (typeof SERVICE_KEYS)[number]

const SERVICE_LABELS: Record<ServiceKey, string> = {
  backend: '后端',
  frontend: '前端',
  worker: 'worker',
}

const STATUS_MAP: Record<
  string,
  { label: string; type: 'success' | 'error' | 'warning' | 'info' }
> = {
  ok: { label: '正常', type: 'success' },
  down: { label: '停止', type: 'error' },
  unhealthy: { label: '异常', type: 'warning' },
  starting: { label: '启动中', type: 'info' },
}

const services = computed(() => health.value?.services ?? null)
const disk = computed(() => health.value?.resources.disk ?? null)
const memory = computed(() => health.value?.resources.memory ?? null)
const logs = computed(() => health.value?.resources.logs ?? null)
const alerts = computed(() => health.value?.alerts ?? [])

function statusTag(status: string) {
  return STATUS_MAP[status] ?? { label: status, type: 'info' as const }
}

async function load() {
  try {
    const { data } = await apiClient.get<ServiceHealth>('/health/services')
    health.value = data
    error.value = ''
  } catch {
    error.value = '无法获取服务健康状态（后端可能未运行）'
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 10000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="service-health">
    <n-alert v-if="error" type="error" style="margin-bottom: 12px">
      {{ error }}
    </n-alert>

    <!-- 服务状态 -->
    <div class="svc-grid">
      <n-card v-for="key in SERVICE_KEYS" :key="key" size="small">
        <div class="svc-row">
          <span class="svc-label">{{ SERVICE_LABELS[key] }}</span>
          <n-tag :type="statusTag(services?.[key]?.status ?? 'down').type" size="small" round>
            {{ statusTag(services?.[key]?.status ?? 'down').label }}
          </n-tag>
        </div>
        <div class="svc-meta">
          <span v-if="key === 'frontend' && services?.frontend?.latency_ms != null">
            延迟 {{ services.frontend.latency_ms }}ms
          </span>
          <span v-else-if="key === 'worker' && services?.worker?.count">
            存活 {{ services.worker.count }} 个
          </span>
          <span v-else-if="services?.[key]?.pid">PID {{ services[key].pid }}</span>
          <span v-else>-</span>
        </div>
      </n-card>
    </div>

    <!-- 资源占用 -->
    <n-card v-if="health" size="small" title="资源占用" style="margin-top: 16px">
      <div v-if="disk" class="res-row">
        <span class="res-label">磁盘</span>
        <n-progress
          type="line"
          :percentage="Math.min(100, disk.used_percent)"
          :status="disk.used_percent >= 90 ? 'error' : 'success'"
          style="flex: 1"
        />
        <span class="res-value">{{ disk.used_percent.toFixed(1) }}%</span>
      </div>
      <div v-if="memory" class="res-row">
        <span class="res-label">内存</span>
        <n-progress
          type="line"
          :percentage="Math.min(100, memory.used_percent)"
          :status="memory.used_percent >= 90 ? 'error' : 'success'"
          style="flex: 1"
        />
        <span class="res-value">{{ memory.used_percent.toFixed(1) }}%</span>
      </div>
      <div v-if="logs" class="res-row">
        <span class="res-label">日志</span>
        <span class="res-value">{{ formatSize(logs.total_bytes) }}</span>
      </div>
    </n-card>

    <!-- 告警 -->
    <div v-if="alerts.length" class="alerts">
      <n-alert v-for="(a, i) in alerts" :key="i" type="warning" style="margin-top: 12px">
        {{ a }}
      </n-alert>
    </div>
  </div>
</template>

<style scoped>
.service-health {
  margin-bottom: 24px;
}

.svc-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.svc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.svc-label {
  font-weight: 600;
}

.svc-meta {
  font-size: 12px;
  color: #999;
}

.res-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.res-row:last-child {
  margin-bottom: 0;
}

.res-label {
  width: 48px;
  color: #666;
  flex-shrink: 0;
}

.res-value {
  width: 80px;
  text-align: right;
  color: #666;
  flex-shrink: 0;
}

@media (max-width: 700px) {
  .svc-grid {
    grid-template-columns: 1fr;
  }
}
</style>
