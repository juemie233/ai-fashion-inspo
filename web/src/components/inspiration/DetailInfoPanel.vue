<script setup lang="ts">
/** 素材详情页右栏：操作按钮 + 基本信息 + 关联人物 + 大标签 + 标签分组。
 *
 * 从 DetailView 模板原样抽出（提取式重构，标记与判定逻辑不变）；数据与动作用
 * props/emit 对接，视图仍是唯一的状态持有者（穿搭大标签/相似推荐 composable 也在视图侧）。
 */

import { IconClose, IconExclamationCircle, IconPlus } from '@arco-design/web-vue/es/icon'
import { getFileUrl, type InspirationDetailOut, type InspirationTagOut } from '@/api/inspirations'
import CategoryTag from '@/components/inspiration/CategoryTag.vue'
import FaceDetectionSection from '@/components/inspiration/FaceDetectionSection.vue'
import OutfitTagSection from '@/components/inspiration/OutfitTagSection.vue'
import PersonLinkSection from '@/components/person/PersonLinkSection.vue'
import { CATEGORY_LABELS } from '@/constants/tag'
import { formatDate } from '@/utils/format'
import { sourceLabel } from '@/utils/sourceLabel'
import type { PersonBrief } from '@shared/types/person'

const props = defineProps<{
  /** 素材详情 */
  detail: InspirationDetailOut
  /** 人脸已确认（锁定）：禁用关联人物栏 */
  faceLocked: boolean
  /** 重新分析进行中 */
  analyzing: boolean
  /** 下载文件名 */
  downloadFileName: string
  /** 原始链接是否可打开（详情页直链校验结果） */
  sourceLinkValid: boolean
  /** 穿搭大标签（composable 计算结果） */
  outfitTags: InspirationTagOut[]
  /** 大标签候选 */
  outfitOptions: Array<{ label: string; value: string }>
  /** 大标签保存中 */
  outfitAdding: boolean
  /** AI 建议中 */
  aiSuggesting: boolean
  /** AI 建议列表 */
  aiSuggestions: string[]
}>()

const emit = defineEmits<{
  (e: 'favorite'): void
  (e: 'rate', value: number): void
  (e: 'openCollection'): void
  (e: 'trash'): void
  (e: 'restore'): void
  (e: 'permanentDelete'): void
  (e: 'copySourceUrl'): void
  (e: 'reanalyze'): void
  (e: 'updateBloggers', list: PersonBrief[]): void
  (e: 'updateModels', list: PersonBrief[]): void
  (e: 'lockChange', locked: boolean): void
  (e: 'addOutfitTags'): void
  (e: 'removeOutfitTag', id: number): void
  (e: 'tagClick', name: string): void
  (e: 'aiSuggestOutfitTags'): void
  (e: 'confirmOutfitTag', name: string): void
  (e: 'confirmAllOutfitTags'): void
  (e: 'dismissOutfitTag', name: string): void
  (e: 'openMissingCorrection'): void
  (e: 'openTagCorrection', tag: InspirationTagOut): void
  (e: 'removeTag', tag: InspirationTagOut): void
}>()

/** 大标签选中项（原视图状态，经 v-model 双向绑定） */
const outfitSelected = defineModel<string[]>('outfitSelected', { required: true })

/** 标签分类中文名（A 类/…；未知分类回退原值） */
const CAT_LABELS = CATEGORY_LABELS

/** 分析状态文本 */
function analysisStatusLabel(): string {
  const status = props.detail.analysis_status
  if (status === 'none') return '尚未分析'
  if (status === 'analyzing') return '分析中...'
  if (status === 'error') return '分析失败'
  return '已分析'
}

/** 按类别分组标签（穿搭大标签已在顶部单独展示，这里跳过去重） */
function groupedTags(): Record<string, InspirationTagOut[]> {
  const groups: Record<string, InspirationTagOut[]> = {}
  for (const t of props.detail.tags) {
    const cat = t.tag.category
    if (cat === 'outfit') continue // 穿搭大标签单独在顶部展示，避免重复渲染
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(t)
  }
  return groups
}
</script>

<template>
  <div class="info-section">
    <!-- 顶部操作 -->
    <div class="info-actions">
      <a-button
        :type="detail.is_favorite ? 'primary' : 'secondary'"
        :status="detail.is_favorite ? 'danger' : undefined"
        @click="emit('favorite')"
      >
        {{ detail.is_favorite ? '❤️ 已收藏' : '🤍 收藏' }}
      </a-button>
      <a-button size="small" @click="emit('openCollection')">📁 加入合集</a-button>
      <!-- 五星评分：仅整数，点击星设置，再点已选星清除（0 分） -->
      <div class="rating-box" title="评分（0~5，点击星设置，再点清除）">
        <a-rate
          :model-value="detail.rating || 0"
          allow-clear
          @change="(v: number) => emit('rate', v)"
        />
        <span v-if="(detail.rating || 0) > 0" class="rating-value">
          {{ detail.rating || 0 }} 分
        </span>
      </div>
      <a :href="getFileUrl(detail.file_path)" :download="downloadFileName" class="download-link">
        <a-button>⬇️ {{ detail.media_type === 'video' ? '下载视频' : '下载原图' }}</a-button>
      </a>
      <template v-if="detail.deleted_at">
        <a-button type="secondary" @click="emit('restore')">恢复</a-button>
        <a-popconfirm content="彻底删除后不可恢复，确定继续？" @ok="emit('permanentDelete')">
          <a-button type="primary" status="danger">彻底删除</a-button>
        </a-popconfirm>
      </template>
      <a-button v-else type="secondary" status="danger" @click="emit('trash')">移入垃圾桶</a-button>
    </div>

    <!-- 基本信息 -->
    <div class="info-meta">
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="来源">
          <a-tag size="small" color="arcoblue">{{ sourceLabel(detail.source_type || '') }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item v-if="detail.source_author" label="作者">
          {{ detail.source_author }}
        </a-descriptions-item>
        <a-descriptions-item v-if="detail.source_url" label="原始链接">
          <a
            v-if="sourceLinkValid"
            :href="detail.source_url"
            target="_blank"
            rel="noopener noreferrer"
            >打开</a
          >
          <a-typography-text v-else type="secondary">图片直链，无法打开</a-typography-text>
          <a-button size="mini" type="text" style="margin-left: 8px" @click="emit('copySourceUrl')"
            >复制</a-button
          >
        </a-descriptions-item>
        <a-descriptions-item label="AI 分析">
          <a-tag
            size="small"
            :color="
              detail.analysis_status === 'done'
                ? 'green'
                : detail.analysis_status === 'error'
                  ? 'red'
                  : 'gray'
            "
          >
            {{ analysisStatusLabel() }}
          </a-tag>
          <a-button
            v-if="detail.analysis_status === 'error' || detail.analysis_status === 'none'"
            size="mini"
            type="text"
            :loading="analyzing"
            style="margin-left: 8px"
            @click="emit('reanalyze')"
            >重新分析</a-button
          >
        </a-descriptions-item>
        <a-descriptions-item label="上传时间">
          {{ formatDate(detail.created_at) }}
        </a-descriptions-item>
      </a-descriptions>
    </div>

    <!-- 关联博主（从已有列表选择添加 / 解除关联；人脸确认锁定后禁用） -->
    <PersonLinkSection
      kind="blogger"
      :persons="detail.bloggers || []"
      :inspiration-id="detail.id"
      :disabled="faceLocked"
      @change="(list: PersonBrief[]) => emit('updateBloggers', list)"
    />

    <!-- 关联模特（从已有列表选择添加 / 解除关联；人脸确认锁定后禁用） -->
    <PersonLinkSection
      kind="model"
      :persons="detail.models || []"
      :inspiration-id="detail.id"
      :disabled="faceLocked"
      @change="(list: PersonBrief[]) => emit('updateModels', list)"
    />

    <!-- 人脸识别（博主特征库匹配） -->
    <FaceDetectionSection
      :inspiration-id="detail.id"
      @lock-change="(v: boolean) => emit('lockChange', v)"
    />

    <!-- 穿搭大标签 -->
    <OutfitTagSection
      :tags="outfitTags"
      :options="outfitOptions"
      v-model:selected="outfitSelected"
      :adding="outfitAdding"
      :ai-suggesting="aiSuggesting"
      :ai-suggestions="aiSuggestions"
      @add="emit('addOutfitTags')"
      @remove="(id: number) => emit('removeOutfitTag', id)"
      @tag-click="(name: string) => emit('tagClick', name)"
      @ai-suggest="emit('aiSuggestOutfitTags')"
      @confirm="(name: string) => emit('confirmOutfitTag', name)"
      @confirm-all="emit('confirmAllOutfitTags')"
      @dismiss="(name: string) => emit('dismissOutfitTag', name)"
    />

    <!-- 标签分组 -->
    <div v-if="detail.tags.length > 0" class="tags-section">
      <div class="tags-header">
        <h4>标签</h4>
        <a-button size="mini" type="text" @click="emit('openMissingCorrection')">
          <template #icon><IconPlus /></template>
          AI 漏标了？补充
        </a-button>
      </div>
      <div v-for="(tags, category) in groupedTags()" :key="category" class="tag-group">
        <span class="tag-category-label">
          {{ CAT_LABELS[category] || category }}
        </span>
        <div class="tag-chips">
          <span
            v-for="t in tags"
            :key="t.tag.id"
            class="tag-clickable"
            @click="emit('tagClick', t.tag.name)"
          >
            <CategoryTag :category="t.tag.category" size="small">
              {{ t.tag.name
              }}<template v-if="t.confidence < 0.8">
                ({{ Math.round(t.confidence * 100) }}%)</template
              >
            </CategoryTag>
            <a-button
              v-if="t.source !== 'manual'"
              size="mini"
              type="text"
              circle
              class="tag-correction-btn"
              title="标错了？反馈给纠错库"
              @click.stop="emit('openTagCorrection', t)"
            >
              <template #icon><IconExclamationCircle /></template>
            </a-button>
            <a-popconfirm
              :content="`确定移除标签「${t.tag.name}」？`"
              ok-text="移除"
              cancel-text="取消"
              @ok="emit('removeTag', t)"
            >
              <a-button
                size="mini"
                type="text"
                circle
                class="tag-remove-btn"
                title="移除该标签"
                @click.stop
              >
                <template #icon><IconClose /></template>
              </a-button>
            </a-popconfirm>
          </span>
        </div>
      </div>
    </div>

    <!-- 无标签 -->
    <a-empty v-else description="暂无标签，AI 分析后会自动生成" />
  </div>
</template>

<style scoped>
/* 右栏样式随标记从 DetailView 迁入：scoped 样式不跨组件生效，留在视图会失效 */
.info-section {
  width: 360px;
  flex-shrink: 0;
}

.info-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

/* 评分控件：与收藏按钮并列，垂直居中 */
.rating-box {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
}

.rating-value {
  font-size: 12px;
  color: #b57914;
  font-weight: 600;
}

.info-meta {
  margin-bottom: 24px;
}

.tags-section h4 {
  margin-bottom: 12px;
  font-size: 16px;
}

/* 标签区标题行：标题 + 「AI 漏标了？补充」入口 */
.tags-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.tag-group {
  margin-bottom: 12px;
}

.tag-category-label {
  font-size: 12px;
  color: #999;
  display: block;
  margin-bottom: 4px;
}

.tag-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 可点击跳转搜索的标签 */
.tag-clickable {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

/* 标签移除按钮：悬停标签时出现，避免常驻造成视觉噪音 */
.tag-remove-btn {
  opacity: 0;
  transition: opacity 0.15s;
  transform: scale(0.85);
}
.tag-clickable:hover .tag-remove-btn {
  opacity: 1;
}

/* 「标错了」反馈按钮：同样悬停出现，颜色弱于移除按钮 */
.tag-correction-btn {
  opacity: 0;
  transition: opacity 0.15s;
  transform: scale(0.85);
  color: var(--color-text-3);
}
.tag-clickable:hover .tag-correction-btn {
  opacity: 1;
}
.tag-correction-btn:hover {
  color: rgb(var(--warning-6));
}

/* 下载原图按钮的链接容器 */
.download-link {
  display: inline-flex;
}

@media (max-width: 900px) {
  .info-section {
    width: 100%;
  }
}
</style>
