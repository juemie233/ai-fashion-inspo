<script setup lang="ts">
/** 合集导出弹窗：选格式 → 生成 → 下载（文件 24h 过期）。
 *
 * 四种格式（用户已确认）：PDF 画册 / ZIP 原图 / CSV 清单 / Markdown 清单。
 * 生成是显式操作（可能有 500 张 && 打包耗时）；成功后展示素材数/跳过数/体积/过期时间，
 * 下载用 `<a download>` 触发，失败显式报错并允许重试。
 */

import { computed, ref, watch } from 'vue'
import {
  exportCollection,
  type CollectionExportFormat,
  type CollectionExportResult,
} from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'
import { formatDateTime, formatSize } from '@/utils/format'

const props = defineProps<{
  visible: boolean
  /** 要导出的合集 id（null 时不生成） */
  collectionId: number | null
  collectionName?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
}>()

interface FormatOption {
  value: CollectionExportFormat
  label: string
  description: string
}

const FORMAT_OPTIONS: FormatOption[] = [
  { value: 'pdf', label: 'PDF 画册', description: '按合集顺序排版成图片画册，适合浏览/打印' },
  { value: 'zip', label: 'ZIP 原图', description: '打包原始图片/视频文件，体积最大' },
  { value: 'csv', label: 'CSV 清单', description: '素材字段表，可用 Excel 打开' },
  { value: 'markdown', label: 'Markdown 清单', description: '带缩略图/来源链接的文本清单' },
]

const format = ref<CollectionExportFormat>('pdf')
const loading = ref(false)
const error = ref<string | null>(null)
const result = ref<CollectionExportResult | null>(null)
/** 生成请求序号：切换合集后旧请求的结果直接丢弃（否则会下载到上一个合集的导出文件） */
let requestSeq = 0

const sizeText = computed(() => (result.value ? formatSize(result.value.size_bytes) : '-'))
const expiresText = computed(() => (result.value ? formatDateTime(result.value.expires_at) : '-'))

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    // 每次打开都是新的导出任务（旧 token 可能已过期）
    format.value = 'pdf'
    loading.value = false
    error.value = null
    result.value = null
  },
)

// 合集被切换（弹窗未关或重新打开另一个合集）：作废在飞的请求并清掉旧结果
watch(
  () => props.collectionId,
  () => {
    requestSeq++
    loading.value = false
    error.value = null
    result.value = null
  },
)

function pickFormat(value: CollectionExportFormat): void {
  if (loading.value) return
  format.value = value
  // 换了格式就作废旧结果，避免下载到与当前选择不一致的文件
  result.value = null
  error.value = null
}

async function generate(): Promise<void> {
  if (!props.collectionId || loading.value) return
  const seq = ++requestSeq
  loading.value = true
  error.value = null
  result.value = null
  try {
    const data = await exportCollection(props.collectionId, format.value)
    // 期间切换了合集/关闭弹窗：结果作废，避免把 A 的文件展示成 B 的
    if (seq !== requestSeq) return
    result.value = data
  } catch (e) {
    if (seq !== requestSeq) return
    // 失败显式报错（常见：文件缺失过多、磁盘空间不足、素材超过 500 张）
    error.value = getApiErrorMessage(e, '生成导出文件失败')
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

/** 用临时 <a download> 触发下载（同源接口地址） */
function download(): void {
  const data = result.value
  if (!data) return
  const link = document.createElement('a')
  link.href = data.download_url
  link.download = data.filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}
</script>

<template>
  <a-modal
    :visible="visible"
    :width="'min(560px, calc(100vw - 32px))'"
    title="导出合集"
    @update:visible="emit('update:visible', $event)"
  >
    <p v-if="collectionName" class="export-collection">合集：{{ collectionName }}</p>

    <!-- 格式单选：卡片式，选中有描边 -->
    <div class="format-options" role="radiogroup" aria-label="导出格式">
      <button
        v-for="option in FORMAT_OPTIONS"
        :key="option.value"
        type="button"
        class="format-option"
        :class="{ active: format === option.value }"
        role="radio"
        :aria-checked="format === option.value"
        :disabled="loading"
        @click="pickFormat(option.value)"
      >
        <span class="format-label">{{ option.label }}</span>
        <span class="format-desc">{{ option.description }}</span>
      </button>
    </div>

    <a-alert v-if="error" type="error" class="export-error" :title="error">
      <template #action>
        <a-button size="mini" :loading="loading" @click="() => void generate()">重试</a-button>
      </template>
    </a-alert>

    <!-- 生成成功：素材数/跳过数/体积/24h 过期 -->
    <div v-if="result" class="export-result">
      <div class="result-grid">
        <div class="result-item">
          <span class="result-label">素材数</span>
          <span class="result-value">{{ result.item_count }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">跳过</span>
          <span class="result-value">{{ result.skipped_count }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">文件大小</span>
          <span class="result-value">{{ sizeText }}</span>
        </div>
        <div class="result-item">
          <span class="result-label">文件名</span>
          <span class="result-value result-file" :title="result.filename">
            {{ result.filename }}
          </span>
        </div>
      </div>
      <a-alert type="warning" class="expire-alert">
        导出文件 24 小时后过期（{{ expiresText }}），请及时下载；过期后可重新生成。
      </a-alert>
    </div>

    <div class="export-hints">
      <p>版权提示：导出内容仅用于个人整理，请尊重原作者版权，勿二次分发。</p>
      <p>体积提示：每个合集最多导出 500 张；ZIP 原图体积可能较大，生成需要一些时间。</p>
    </div>

    <template #footer>
      <div class="export-footer">
        <a-button size="small" @click="emit('update:visible', false)">关闭</a-button>
        <a-button
          v-if="!result"
          size="small"
          type="primary"
          :loading="loading"
          :disabled="!collectionId"
          @click="() => void generate()"
        >
          {{ loading ? '正在生成…' : '生成导出文件' }}
        </a-button>
        <template v-else>
          <a-button size="small" :loading="loading" @click="() => void generate()">
            重新生成
          </a-button>
          <a-button size="small" type="primary" @click="download">下载文件</a-button>
        </template>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.export-collection {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--ink-2);
}

.format-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.format-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  text-align: left;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.format-option:hover:not(:disabled) {
  border-color: var(--line-strong);
}

.format-option.active {
  border-color: var(--pine);
  background: var(--paper);
}

.format-label {
  font-size: 13px;
  color: var(--ink);
}

.format-desc {
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink-3);
}

.export-error {
  margin-top: 12px;
}

.export-result {
  margin-top: 14px;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.result-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.result-label {
  font-size: 11px;
  color: var(--ink-3);
}

.result-value {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums lining-nums;
  font-size: 16px;
  color: var(--ink);
}

.result-file {
  font-family: inherit;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.expire-alert {
  margin-top: 10px;
}

.export-hints {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.export-hints p {
  margin: 0 0 4px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--ink-3);
}

.export-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .format-options,
  .result-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
