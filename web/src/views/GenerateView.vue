<script setup lang="ts">
/** 本地出图页：填参数 → 生成 → 候选区勾选 → 确认入库。
 *
 * 为什么两段式（生成 / 入库分开）：本项目一贯「宁缺毋滥、手动确认」——
 * 生成 4 张里通常只要 1~2 张，直接入库会让废图占库容、进统计、还进打标队列烧算力。
 *
 * 显存现实（实测）：16 GB 卡上 1024² 峰值已到 15.1 GiB，所以尺寸只给预设、张数上限 4，
 * 且生成时会与打标任务互斥（后端 GPU 互斥组），必要时排队等 Ollama 释放显存。
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { useImageGen } from '@/composables/useImageGen'
import GenerateCandidatesPanel from '@/components/generate/GenerateCandidatesPanel.vue'
import GenerateHistoryTable from '@/components/generate/GenerateHistoryTable.vue'
import GenerateParamForm from '@/components/generate/GenerateParamForm.vue'
import ImageLightbox from '@/components/inspiration/ImageLightbox.vue'
import { discardRun } from '@/api/generate'
import { getApiErrorMessage } from '@/utils/apiError'

const route = useRoute()
/** 删除某条生成记录（连同批次目录里的候选图）；已入库的素材不受影响 */
const deletingId = ref<number | null>(null)

async function removeRun(id: number) {
  deletingId.value = id
  try {
    const r = await discardRun(id)
    Message.success(`已删除（清理 ${r.removed_files ?? 0} 个文件）`)
    // 删掉的正是当前打开的批次：清空详情区，否则用户还能对已删除的批次点「确认入库」
    if (run.value?.id === id) reset()
    await loadHistory()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
  } finally {
    deletingId.value = null
  }
}

const {
  status,
  statusLoading,
  submitting,
  run,
  candidates,
  selected,
  active,
  stageText,
  lastSeed,
  reset,
  history,
  historyLoading,
  loadStatus,
  submit,
  toggle,
  selectAll,
  clearSelection,
  confirm,
  cancel,
  promptFrom,
  discard,
  stopPoll,
  loadHistory,
  retry,
  regenerate,
} = useImageGen()

// ── 候选图大图浏览 ──
/** 灯箱开关与起始索引：缩略图看不清面料/版型，点开可按 100% 看细节并左右翻整批 */
const lightboxVisible = ref(false)
const lightboxIndex = ref(0)
const candidateUrls = computed(() => candidates.value.map((c) => c.url))

function openLightbox(index: number): void {
  if (!candidates.value.length) return
  lightboxIndex.value = Math.max(0, Math.min(index, candidates.value.length - 1))
  lightboxVisible.value = true
}

/** 候选图 URL 由后端直接给出（/api/generate/...），不能再套素材库的 /api/files/ 前缀 */
function resolveCandidateUrl(path: string): string {
  return path
}

const ready = computed(() => status.value?.ready === true)

/** 历史刷新：批次进入终态时拉一次，避免用户手动刷新页面 */
watch(
  () => run.value?.status,
  (st) => {
    if (st && ['done', 'failed', 'cancelled'].includes(st)) void loadHistory()
  },
)

/** 从素材详情「用这张图改款」跳进来时带的参考素材 ID（交给参数面板预填并切档位） */
const initialRefIds = typeof route.query.ref === 'string' ? route.query.ref.trim() : ''

onMounted(() => {
  void loadStatus()
  void loadHistory()
})
onUnmounted(stopPoll)
</script>

<template>
  <div class="gen-page">
    <div class="gen-head">
      <div>
        <h2 class="gen-title">本地出图</h2>
        <div class="gen-sub">
          用本机 ComfyUI 生成穿搭图；生成结果先进候选区，勾选后才入库（默认待审核、标记「AI
          生成」）。
        </div>
      </div>
      <a-button :loading="statusLoading" @click="loadStatus">刷新环境</a-button>
    </div>

    <!-- 环境未就绪：给可执行引导，而不是让用户点了才失败 -->
    <a-alert v-if="status && !ready" type="warning" class="gen-alert">
      {{ status.reason || '出图环境未就绪' }}
      <div class="gen-hint">
        启动方式：双击 <code>D:\AI\ComfyUI\run_nvidia_gpu.bat</code>，等浏览器能打开
        <code>http://127.0.0.1:8188</code> 后点「刷新环境」。
      </div>
    </a-alert>
    <a-alert v-else-if="status" type="success" class="gen-alert">
      ComfyUI {{ status.comfyui.version }} 就绪 · 显存 {{ status.comfyui.vram_free_gib }} /
      {{ status.comfyui.vram_total_gib }} GiB 可用
      <span v-if="status.ollama_resident.length" class="gen-hint">
        · Ollama 占用：{{
          status.ollama_resident.map((m) => m.name).join('、')
        }}（出图会等待其释放）
      </span>
    </a-alert>

    <div class="gen-grid">
      <!-- 左：参数 -->
      <a-card class="gen-card" :bordered="false">
        <GenerateParamForm
          :status="status"
          :ready="ready"
          :submitting="submitting"
          :active="active"
          :initial-ref-ids="initialRefIds"
          :prompt-from="promptFrom"
          @submit="submit"
          @cancel="cancel"
        />
      </a-card>

      <!-- 右：进度与候选 -->
      <a-card class="gen-card" :bordered="false">
        <GenerateCandidatesPanel
          :run="run"
          :candidates="candidates"
          :selected="selected"
          :stage-text="stageText"
          :last-seed="lastSeed"
          :submitting="submitting"
          @preview="openLightbox"
          @toggle="toggle"
          @regenerate="regenerate"
          @select-all="selectAll"
          @clear-selection="clearSelection"
          @confirm="confirm"
          @discard="discard"
        />
      </a-card>
    </div>

    <!-- 候选图大图浏览：复用素材库的灯箱（缩放/拖动/左右切换都有） -->
    <ImageLightbox
      :show="lightboxVisible"
      :image-paths="candidateUrls"
      :initial-index="lightboxIndex"
      :url-resolver="resolveCandidateUrl"
      @close="lightboxVisible = false"
    />

    <!-- 生成历史：同参重跑入口（历史记录只存参数，图在候选区，过期会被清理） -->
    <a-card class="gen-card gen-history" :bordered="false">
      <GenerateHistoryTable
        :history="history"
        :loading="historyLoading"
        :submitting="submitting"
        :deleting-id="deletingId"
        :status="status"
        @refresh="loadHistory"
        @retry="retry"
        @remove="removeRun"
      />
    </a-card>
  </div>
</template>

<style scoped>
.gen-page {
  padding: 16px;
}
.gen-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.gen-title {
  margin: 0 0 4px;
  font-size: 18px;
}
.gen-sub {
  color: #8a8f99;
  font-size: 12px;
}
.gen-alert {
  margin-bottom: 12px;
}
.gen-hint {
  color: #8a8f99;
  font-size: 12px;
  line-height: 1.7;
}
.gen-hint code {
  padding: 0 3px;
  border-radius: 3px;
  background: #f2f3f5;
}
.gen-grid {
  display: grid;
  grid-template-columns: minmax(320px, 420px) 1fr;
  gap: 12px;
}
@media (max-width: 900px) {
  .gen-grid {
    grid-template-columns: 1fr;
  }
}
.gen-card {
  background: #fff;
}
.gen-label {
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.gen-result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.gen-empty {
  color: #8a8f99;
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}
.gen-history {
  margin-top: 12px;
}
</style>
