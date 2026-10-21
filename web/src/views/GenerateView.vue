<script setup lang="ts">
/** 本地出图页：填参数 → 生成 → 候选区勾选 → 确认入库。
 *
 * 为什么两段式（生成 / 入库分开）：本项目一贯「宁缺毋滥、手动确认」——
 * 生成 4 张里通常只要 1~2 张，直接入库会让废图占库容、进统计、还进打标队列烧算力。
 *
 * 显存现实（实测）：16 GB 卡上 1024² 峰值已到 15.1 GiB，所以尺寸只给预设、张数上限 4，
 * 且生成时会与打标任务互斥（后端 GPU 互斥组），必要时排队等 Ollama 释放显存。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useImageGen } from '@/composables/useImageGen'
import { getFileUrl } from '@/api/inspirations'

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
} = useImageGen()

// ── 参数 ──
const prompt = ref('')
const negative = ref('')
const count = ref(2)
const steps = ref(8)
const seed = ref(0)
/** 尺寸预设：不给自由输入，避免用户填出必 OOM 的值（见 docs 第 8 节） */
const sizePresets = [
  { label: '1024 × 1024（方形，最快）', value: '1024x1024' },
  { label: '1328 × 1328（更大，接近显存上限）', value: '1328x1328' },
  { label: '768 × 1344（竖版，适合穿搭）', value: '768x1344' },
]
const size = ref('1024x1024')
const [width, height] = computed(() => size.value.split('x').map(Number)).value

// ── 从素材反推 prompt ──
const refIdsText = ref('')
const previewing = ref(false)
async function onPreviewPrompt() {
  const ids = refIdsText.value
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!ids.length) return
  previewing.value = true
  try {
    const text = await promptFrom(ids)
    if (text) prompt.value = text
  } finally {
    previewing.value = false
  }
}

// ── 生成与入库 ──
async function onSubmit() {
  if (!prompt.value.trim()) return
  await submit({
    prompt: prompt.value.trim(),
    negative: negative.value.trim() || undefined,
    width,
    height,
    steps: steps.value,
    count: count.value,
    seed: seed.value,
  })
}

const canSubmit = computed(() => !!prompt.value.trim() && !submitting.value && !active.value)

const activePreset = computed(() => sizePresets.find((p) => p.value === size.value))
const selectedCount = computed(() => selected.value.size)
const ready = computed(() => status.value?.ready === true)

onMounted(loadStatus)
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
        <div class="gen-label">描述（prompt）</div>
        <a-textarea
          v-model="prompt"
          placeholder="例：米色针织开衫配白色阔腿裤，极简工作室背景，柔和自然光，时尚编辑摄影"
          :auto-size="{ minRows: 4, maxRows: 8 }"
          allow-clear
        />

        <div class="gen-row">
          <a-input
            v-model="refIdsText"
            placeholder="从素材反推：粘贴素材 ID（逗号分隔）"
            allow-clear
          />
          <a-button :loading="previewing" @click="onPreviewPrompt">反推</a-button>
        </div>
        <div class="gen-hint">反推会读取这些素材的标签、正文与主色拼成描述（可再手动改）。</div>

        <div class="gen-label">负向描述（可选）</div>
        <a-input v-model="negative" placeholder="例：模糊、变形、多余手指" allow-clear />

        <div class="gen-row gen-row--multi">
          <a-select v-model="size" :options="sizePresets" style="flex: 1" />
          <a-input-number v-model="steps" :min="1" :max="30" style="width: 110px">
            <template #prefix>步数</template>
          </a-input-number>
          <a-input-number v-model="count" :min="1" :max="4" style="width: 100px">
            <template #prefix>张数</template>
          </a-input-number>
        </div>
        <div class="gen-row">
          <a-input-number v-model="seed" :min="0" style="width: 180px">
            <template #prefix>seed</template>
          </a-input-number>
          <span class="gen-hint">0 = 随机（提交后会把真实 seed 显示在结果区，便于复现）</span>
        </div>

        <div class="gen-actions">
          <a-button
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit || !ready"
            @click="onSubmit"
          >
            生成 {{ count }} 张
          </a-button>
          <a-button v-if="active" status="warning" @click="cancel">取消</a-button>
          <span v-if="activePreset" class="gen-hint">{{ activePreset.label }}</span>
        </div>
      </a-card>

      <!-- 右：进度与候选 -->
      <a-card class="gen-card" :bordered="false">
        <div class="gen-result-head">
          <span class="gen-label" style="margin: 0">生成结果</span>
          <span v-if="lastSeed" class="gen-hint">本次 seed 基值 {{ lastSeed }}</span>
        </div>

        <div v-if="!run" class="gen-empty">还没有生成结果：左侧填好描述后点「生成」。</div>
        <template v-else>
          <a-alert
            :type="run.status === 'failed' ? 'error' : run.status === 'done' ? 'success' : 'info'"
            class="gen-alert"
          >
            {{ stageText }}
          </a-alert>

          <div v-if="run.status === 'done' && candidates.length" class="gen-toolbar">
            <a-button size="mini" @click="selectAll">全选未入库</a-button>
            <a-button size="mini" @click="clearSelection">清空选择</a-button>
            <span class="gen-hint">已选 {{ selectedCount }} / {{ candidates.length }}</span>
            <a-button type="primary" size="mini" :disabled="selectedCount === 0" @click="confirm">
              确认入库
            </a-button>
            <a-button size="mini" status="danger" @click="discard">丢弃本批</a-button>
          </div>

          <div v-if="candidates.length" class="gen-candidates">
            <div
              v-for="item in candidates"
              :key="item.filename"
              class="gen-item"
              :class="{ 'is-selected': selected.has(item.filename) }"
              @click="!item.imported && toggle(item.filename)"
            >
              <img :src="getFileUrl(item.url)" :alt="item.filename" loading="lazy" />
              <div class="gen-item-meta">
                <span>seed {{ item.seed }}</span>
                <a-tag v-if="item.imported" size="small" color="green">已入库</a-tag>
              </div>
            </div>
          </div>
          <div v-else-if="run.status === 'done'" class="gen-empty">
            本批没有产出（可能已丢弃或全部失败）。
          </div>
        </template>
      </a-card>
    </div>
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
.gen-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.gen-row--multi {
  flex-wrap: wrap;
}
.gen-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
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
.gen-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.gen-candidates {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.gen-item {
  border: 2px solid transparent;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  background: #f7f8fa;
}
.gen-item:hover {
  border-color: #94bfff;
}
.gen-item.is-selected {
  border-color: #165dff;
}
.gen-item img {
  width: 100%;
  display: block;
}
.gen-item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px;
  font-size: 11px;
  color: #8a8f99;
}
</style>
