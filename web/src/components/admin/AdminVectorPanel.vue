<script setup lang="ts">
/** 向量管理面板：展示向量化状态，一键为缺失向量的素材创建回填任务。
 *
 * 背景：详情页相似推荐对「没有图像向量」的素材会现场做 CLIP 编码（单张数秒），
 * 导致打开素材明显卡顿。本面板提供「一键向量化缺失素材」入口（异步任务，
 * 由 worker 执行，进度通过任务中心查看）。
 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { useAdminTask } from '@/composables/useAdminTask'
import type { VectorStats } from '@/types/admin'

const stats = ref<VectorStats | null>(null)
const loading = ref(false)
const submitting = ref(false)

// 后台任务轮询（向量回填）
const { adminTask, startAdminPolling, stopAdminPolling, resumeAdminTask } = useAdminTask()

/** 任务是否进行中（pending/running）——进行中按钮必须禁用，防止重复创建任务 */
const taskRunning = computed(
  () =>
    adminTask.value !== null &&
    (adminTask.value.status === 'pending' || adminTask.value.status === 'running'),
)

/** 按钮禁用条件：提交中、任务进行中、无缺失、lancedb 不可用 */
const backfillDisabled = computed(
  () =>
    submitting.value ||
    taskRunning.value ||
    stats.value === null ||
    stats.value.missing === 0 ||
    !stats.value.lancedb_available,
)

/** 文本向量是否为旧公式版本（需全量重建才能启用正文 caption 语义搜索） */
const textVectorStale = computed(() => stats.value?.text_vector_version?.stale ?? false)

const rebuildingText = ref(false)

/** 重建文本向量按钮禁用条件：提交中、任务进行中、无文本向量、lancedb 不可用 */
const rebuildTextDisabled = computed(
  () =>
    rebuildingText.value ||
    taskRunning.value ||
    stats.value === null ||
    stats.value.text_vectors === 0 ||
    !stats.value.lancedb_available,
)

async function loadStats() {
  loading.value = true
  try {
    const { data } = await apiClient.get<VectorStats>('/admin/vector-stats')
    stats.value = data
  } catch {
    Message.error('加载向量化状态失败')
  } finally {
    loading.value = false
  }
}

/** 一键向量化：创建回填任务并开始轮询进度 */
async function handleBackfill() {
  if (!stats.value || stats.value.missing === 0 || taskRunning.value) return
  submitting.value = true
  try {
    const { data } = await apiClient.post<{
      task_id: number | null
      count: number
      message: string
    }>('/admin/vector-backfill')
    if (data.task_id) {
      Message.success(`已创建向量回填任务 #${data.task_id}（${data.count} 个素材）`)
      startAdminPolling(data.task_id, () => {
        Message.success('向量回填完成，素材打开将不再卡顿')
        loadStats()
      })
    } else {
      Message.info(data.message || '没有缺失向量的素材')
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建向量回填任务失败'))
  } finally {
    submitting.value = false
  }
}

/** 全量重建文本向量：文本公式升级（正文 caption 参与语义搜索）后使用，
 * 跳过图像向量避免无谓的 CLIP 全库编码 */
async function handleRebuildText() {
  if (rebuildTextDisabled.value) return
  rebuildingText.value = true
  try {
    const { data } = await apiClient.post<{
      task_id: number | null
      count: number
      message: string
    }>('/admin/vector-backfill', { rebuild_text: true })
    if (data.task_id) {
      Message.success(`已创建文本向量重建任务 #${data.task_id}（${data.count} 个素材）`)
      startAdminPolling(data.task_id, () => {
        Message.success('文本向量重建完成，正文 caption 已参与语义搜索')
        loadStats()
      })
    } else {
      Message.info(data.message || '没有可重建文本向量的素材')
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建文本向量重建任务失败'))
  } finally {
    rebuildingText.value = false
  }
}

onMounted(async () => {
  await loadStats()
  // 刷新后恢复进行中的向量回填任务轮询（任务完成后按钮自动恢复可用）
  resumeAdminTask(() => {
    Message.success('向量回填完成')
    loadStats()
  })
})

// 离开管理页时停止任务轮询，避免 1 秒间隔的请求与弹窗残留
onUnmounted(() => {
  stopAdminPolling()
})
</script>

<template>
  <a-card size="small" title="向量管理" class="vector-panel">
    <a-spin :loading="loading">
      <template v-if="stats">
        <!-- 向量化状态统计 -->
        <div class="stat-grid">
          <a-statistic title="图片素材" :value="stats.total_inspirations" />
          <a-statistic title="已入库图像向量" :value="stats.image_vectors" />
          <a-statistic
            title="缺失（待向量化）"
            :value="stats.missing"
            :value-style="{ color: stats.missing > 0 ? '#e8804f' : undefined }"
          />
          <a-statistic title="文本向量" :value="stats.text_vectors" />
        </div>

        <a-alert v-if="!stats.lancedb_available" type="warning" style="margin-top: 12px">
          未检测到 lancedb，向量功能不可用。请先执行：<a-typography-text code
            >pip install lancedb</a-typography-text
          >
        </a-alert>

        <a-alert v-else-if="textVectorStale" type="warning" style="margin-top: 12px">
          文本向量公式已升级（正文 caption 已参与语义搜索），存量文本向量为旧版本。
          点击下方「重建文本向量」后，即可按笔记正文描述词进行语义搜索（异步任务，可到「任务管理」查看进度）。
        </a-alert>

        <a-alert v-else type="info" style="margin-top: 12px">
          打开素材详情卡顿的常见原因：素材尚未生成图像向量，相似推荐会现场做 CLIP 编码。
          点击下方按钮为缺失向量的素材批量回填（异步任务，可到「任务管理」查看进度）。
        </a-alert>

        <!-- 一键回填 -->
        <a-space style="margin-top: 16px" align="center" wrap>
          <a-button
            type="primary"
            :loading="submitting"
            :disabled="backfillDisabled"
            @click="handleBackfill"
          >
            {{
              taskRunning
                ? '向量化任务进行中…'
                : stats.missing > 0
                  ? `一键向量化缺失素材（${stats.missing} 个）`
                  : '一键向量化缺失素材'
            }}
          </a-button>
          <a-button
            v-if="textVectorStale"
            type="secondary"
            :loading="rebuildingText"
            :disabled="rebuildTextDisabled"
            @click="handleRebuildText"
          >
            重建文本向量（启用正文语义搜索）
          </a-button>
          <a-button type="secondary" :disabled="taskRunning" @click="loadStats">刷新统计</a-button>
        </a-space>

        <!-- 任务进度 -->
        <div
          v-if="adminTask && (adminTask.status === 'pending' || adminTask.status === 'running')"
          style="margin-top: 16px"
        >
          <a-typography-text type="secondary" style="display: block; margin-bottom: 6px">
            向量回填任务 #{{ adminTask.id }}：{{ adminTask.progress }}%（{{ adminTask.done }}/{{
              adminTask.total
            }}）
          </a-typography-text>
          <a-progress type="line" :percent="adminTask.progress / 100" :stroke-width="8" />
        </div>
      </template>
    </a-spin>
  </a-card>
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
