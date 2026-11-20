<script setup lang="ts">
/** 合集 AI 整理预览弹窗：本地模型生成风格总结/候选名/理由/主题标签/分组。
 *
 * 关键交互（用户已确认）：
 * - **打开弹窗不自动生成**：必须手动点「开始生成」，避免误烧本地模型；
 * - 生成结果只是预览：候选名/描述由用户点「采用」才写回合集；
 * - 收藏理由、主题标签只展示 + 复制，不自动保存；
 * - 分组缩略图可点进素材详情；失败显式报错并可重试。
 */

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import {
  aiSummarizeCollection,
  updateCollection,
  type CollectionAiGroupItem,
  type CollectionAiSummaryOut,
  type CollectionOut,
} from '@/api/collections'
import { getFileUrl } from '@/api/inspirations'
import { getApiErrorMessage } from '@/utils/apiError'
import { copyToClipboard } from '@/utils/clipboard'
import { formatDateTime } from '@/utils/format'
import { openInspiration } from '@/utils/openInspiration'

const props = defineProps<{
  visible: boolean
  /** 当前合集（为空时不能生成） */
  collection: CollectionOut | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  /** 用户采用了候选名或总结作为描述：父级刷新合集列表 */
  (e: 'applied'): void
}>()

const router = useRouter()

const loading = ref(false)
const error = ref<string | null>(null)
const result = ref<CollectionAiSummaryOut | null>(null)
/** 正在采用的候选名下标（-1 = 无） */
const adoptingName = ref(-1)
const applyingDescription = ref(false)

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    // 每次打开回到「待开始」状态：不自动调用 AI 接口
    loading.value = false
    error.value = null
    result.value = null
    adoptingName.value = -1
    applyingDescription.value = false
  },
)

async function generate(): Promise<void> {
  const current = props.collection
  if (!current || loading.value) return
  loading.value = true
  error.value = null
  result.value = null
  try {
    result.value = await aiSummarizeCollection(current.id)
  } catch (e) {
    error.value = getApiErrorMessage(e, 'AI 整理生成失败，请确认本地模型服务后重试')
  } finally {
    loading.value = false
  }
}

/** 采用候选名：直接改合集名（用户显式操作后才写） */
async function adoptName(name: string, index: number): Promise<void> {
  const current = props.collection
  if (!current || adoptingName.value !== -1) return
  adoptingName.value = index
  try {
    await updateCollection(current.id, { name })
    Message.success(`已采用名称「${name}」`)
    emit('applied')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '改名失败'))
  } finally {
    adoptingName.value = -1
  }
}

/** 采用风格总结作为合集描述（同样需用户点击，不自动保存） */
async function applySummaryAsDescription(): Promise<void> {
  const current = props.collection
  const summary = result.value?.summary?.trim()
  if (!current || !summary || applyingDescription.value) return
  applyingDescription.value = true
  try {
    await updateCollection(current.id, { description: summary })
    Message.success('已采用为合集描述')
    emit('applied')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存描述失败'))
  } finally {
    applyingDescription.value = false
  }
}

async function copyText(text: string, label: string): Promise<void> {
  const ok = await copyToClipboard(text)
  if (ok) Message.success(`${label}已复制`)
  else Message.error('复制失败，请手动选择文本')
}

/** 复制收藏理由（模板里不直接闭包引用可能为 null 的 result） */
function copyReason(): void {
  if (result.value) void copyText(result.value.reason, '收藏理由')
}

/** 复制全部主题标签（顿号分隔） */
function copyThemeTags(): void {
  if (result.value) void copyText(result.value.theme_tags.join('、'), '主题标签')
}

function groupThumb(item: CollectionAiGroupItem): string {
  return item.thumbnail_path ? getFileUrl(item.thumbnail_path) : ''
}

function openGroupItem(item: CollectionAiGroupItem): void {
  openInspiration(router, item.id, {})
}
</script>

<template>
  <a-modal
    :visible="visible"
    :width="'min(760px, calc(100vw - 32px))'"
    title="AI 整理预览"
    @update:visible="emit('update:visible', $event)"
  >
    <p v-if="collection" class="ai-collection">合集：{{ collection.name }}</p>

    <!-- 初始态：明确「手动开始」，不自动消耗本地模型 -->
    <div v-if="!result && !loading && !error" class="ai-placeholder">
      <p>点击下方「开始生成」，让本地模型阅读当前合集并给出风格总结、候选名称与分组建议。</p>
      <p class="ai-placeholder-note">
        生成需要一些时间且会占用本地显卡；生成结果只是预览，不会自动修改合集。
      </p>
    </div>

    <div v-if="loading" class="ai-loading">
      <a-spin />
      <p>正在让本地模型阅读合集…（生成结果不会自动保存）</p>
    </div>

    <a-alert v-if="error" type="error" class="ai-error" :title="error">
      <template #action>
        <a-button size="mini" :loading="loading" @click="() => void generate()">重试</a-button>
      </template>
    </a-alert>

    <template v-if="result">
      <section class="ai-section">
        <div class="ai-section-head">
          <h3>风格总结</h3>
          <a-button
            size="mini"
            :loading="applyingDescription"
            @click="() => void applySummaryAsDescription()"
          >
            采用为合集描述
          </a-button>
        </div>
        <p class="ai-summary">{{ result.summary }}</p>
      </section>

      <section class="ai-section">
        <div class="ai-section-head">
          <h3>候选名称</h3>
          <span class="ai-section-hint">点「采用」才会改名</span>
        </div>
        <div class="name-list">
          <div v-for="(name, index) in result.name_suggestions" :key="name" class="name-row">
            <span class="name-text">{{ name }}</span>
            <a-button
              size="mini"
              :loading="adoptingName === index"
              :disabled="adoptingName !== -1"
              @click="() => void adoptName(name, index)"
            >
              采用
            </a-button>
          </div>
        </div>
      </section>

      <section class="ai-section">
        <div class="ai-section-head">
          <h3>收藏理由</h3>
          <a-button size="mini" @click="copyReason">复制理由</a-button>
        </div>
        <p class="ai-reason">{{ result.reason }}</p>
      </section>

      <section class="ai-section">
        <div class="ai-section-head">
          <h3>主题标签</h3>
          <a-button size="mini" :disabled="result.theme_tags.length === 0" @click="copyThemeTags">
            复制标签
          </a-button>
        </div>
        <div class="theme-tags">
          <a-tag v-for="tag in result.theme_tags" :key="tag" size="small">{{ tag }}</a-tag>
          <span v-if="result.theme_tags.length === 0" class="ai-empty">无</span>
        </div>
        <p class="ai-section-hint">主题标签只预览/复制，不会自动写入素材标签。</p>
      </section>

      <section class="ai-section">
        <div class="ai-section-head">
          <h3>风格分组</h3>
          <span class="ai-section-hint">共 {{ result.groups.length }} 组</span>
        </div>
        <div class="group-list">
          <div v-for="group in result.groups" :key="group.tag + group.name" class="group-card">
            <div class="group-head">
              <span class="group-name">{{ group.name }}</span>
              <span class="group-meta">{{ group.tag }} · {{ group.count }} 个素材</span>
            </div>
            <div class="group-thumbs">
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="group-thumb"
                :title="item.source_author ? `@${item.source_author}` : item.id"
                @click="openGroupItem(item)"
              >
                <img v-if="groupThumb(item)" :src="groupThumb(item)" alt="" loading="lazy" />
                <span v-else class="group-thumb-empty">无图</span>
              </button>
              <span v-if="group.items.length === 0" class="ai-empty">无缩略图</span>
            </div>
          </div>
        </div>
      </section>

      <p class="ai-footer-note">
        模型：{{ result.model }} · 生成于 {{ formatDateTime(result.generated_at) }}
      </p>
    </template>

    <template #footer>
      <div class="ai-footer">
        <a-button size="small" @click="emit('update:visible', false)">关闭</a-button>
        <a-button
          v-if="!result"
          size="small"
          type="primary"
          :loading="loading"
          :disabled="!collection"
          @click="() => void generate()"
        >
          {{ loading ? '正在生成…' : '开始生成' }}
        </a-button>
        <a-button
          v-else
          size="small"
          type="primary"
          :loading="loading"
          @click="() => void generate()"
        >
          重新生成
        </a-button>
      </div>
    </template>
  </a-modal>
</template>

<style scoped>
.ai-collection {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--ink-2);
}

.ai-placeholder {
  padding: 20px 18px;
  background: var(--sheet-2);
  border: 1px dashed var(--line);
  border-radius: var(--radius-md);
  text-align: center;
  color: var(--ink-2);
  font-size: 13px;
  line-height: 1.8;
}

.ai-placeholder p {
  margin: 0;
}

.ai-placeholder-note {
  margin-top: 6px !important;
  font-size: 12px;
  color: var(--ink-3);
}

.ai-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 0;
  color: var(--ink-2);
  font-size: 13px;
}

.ai-error {
  margin-bottom: 12px;
}

.ai-section {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.ai-section:first-of-type {
  border-top: 0;
  padding-top: 0;
}

.ai-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.ai-section-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.ai-section-hint {
  font-size: 11px;
  color: var(--ink-3);
}

.ai-summary,
.ai-reason {
  margin: 0;
  font-size: 13px;
  line-height: 1.9;
  color: var(--ink-2);
  white-space: pre-wrap;
}

.name-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.name-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
}

.theme-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.group-card {
  padding: 10px 12px;
  background: var(--sheet);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.group-name {
  font-size: 13px;
  color: var(--ink);
}

.group-meta {
  font-size: 11px;
  color: var(--ink-3);
}

.group-thumbs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.group-thumb {
  width: 54px;
  height: 72px;
  padding: 0;
  overflow: hidden;
  background: var(--sheet-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.group-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.group-thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 10px;
  color: var(--ink-3);
}

.ai-empty {
  font-size: 12px;
  color: var(--ink-3);
}

.ai-footer-note {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--ink-3);
  font-family: var(--font-numeric);
}

.ai-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .group-thumb {
    width: 46px;
    height: 62px;
  }
}
</style>
