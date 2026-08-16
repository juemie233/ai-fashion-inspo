<script setup lang="ts">
/** 向量管理面板：展示向量化状态，一键为缺失向量的素材创建回填任务。
 *
 * 背景：详情页相似推荐对「没有图像向量」的素材会现场做 CLIP 编码（单张数秒），
 * 导致打开素材明显卡顿。本面板提供「一键向量化缺失素材」入口（异步任务，
 * 由 worker 执行，进度通过任务中心查看）。
 */

import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { useAdminTask } from '@/composables/useAdminTask'
import type { VectorStats } from '@/types/admin'

const message = useMessage()
const stats = ref<VectorStats | null>(null)
const loading = ref(false)
const submitting = ref(false)

// 后台任务轮询（向量回填）
const { adminTask, startAdminPolling, stopAdminPolling, resumeAdminTask } = useAdminTask()

async function loadStats() {
  loading.value = true
  try {
    const { data } = await apiClient.get<VectorStats>('/admin/vector-stats')
    stats.value = data
  } catch {
    message.error('加载向量化状态失败')
  } finally {
    loading.value = false
  }
}

/** 一键向量化：创建回填任务并开始轮询进度 */
async function handleBackfill() {
  if (!stats.value || stats.value.missing === 0) return
  submitting.value = true
  try {
    const { data } = await apiClient.post<{ task_id: number | null; count: number; message: string }>(
      '/admin/vector-backfill',
    )
    if (data.task_id) {
      message.success(`已创建向量回填任务 #${data.task_id}（${data.count} 个素材）`)
      startAdminPolling(data.task_id, () => {
        message.success('向量回填完成，素材打开将不再卡顿')
        loadStats()
      })
    } else {
      message.info(data.message || '没有缺失向量的素材')
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建向量回填任务失败')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await loadStats()
  // 刷新后恢复进行中的向量回填任务轮询
  resumeAdminTask(() => {
    message.success('向量回填完成')
    loadStats()
  })
})
</script>

<template>
  <n-card size="small" title="向量管理" class="vector-panel">
    <n-spin :show="loading">
      <template v-if="stats">
        <!-- 向量化状态统计 -->
        <div class="stat-grid">
          <n-statistic label="图片素材" :value="stats.total_inspirations" />
          <n-statistic label="已入库图像向量" :value="stats.image_vectors" />
          <n-statistic
            label="缺失（待向量化）"
            :value="stats.missing"
            :value-style="{ color: stats.missing > 0 ? '#e8804f' : undefined }"
          />
          <n-statistic label="文本向量" :value="stats.text_vectors" />
        </div>

        <n-alert v-if="!stats.lancedb_available" type="warning" style="margin-top: 12px">
          未检测到 lancedb，向量功能不可用。请先执行：<n-text code>pip install lancedb</n-text>
        </n-alert>

        <n-alert v-else type="info" style="margin-top: 12px">
          打开素材详情卡顿的常见原因：素材尚未生成图像向量，相似推荐会现场做 CLIP 编码。
          点击下方按钮为缺失向量的素材批量回填（异步任务，可到「任务管理」查看进度）。
        </n-alert>

        <!-- 一键回填 -->
        <n-space style="margin-top: 16px" align="center">
          <n-button
            type="primary"
            :loading="submitting"
            :disabled="stats.missing === 0 || !stats.lancedb_available"
            @click="handleBackfill"
          >
            一键向量化缺失素材{{ stats.missing > 0 ? `（${stats.missing} 个）` : '' }}
          </n-button>
          <n-button secondary @click="loadStats">刷新统计</n-button>
        </n-space>

        <!-- 任务进度 -->
        <div v-if="adminTask && (adminTask.status === 'pending' || adminTask.status === 'running')" style="margin-top: 16px">
          <n-text depth="2" style="display: block; margin-bottom: 6px">
            向量回填任务 #{{ adminTask.id }}：{{ adminTask.progress }}%（{{ adminTask.done }}/{{ adminTask.total }}）
          </n-text>
          <n-progress type="line" :percentage="adminTask.progress" :height="8" />
        </div>
      </template>
    </n-spin>
  </n-card>
</template>

<style scoped>
.vector-panel {
  margin-bottom: 12px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}
</style>
