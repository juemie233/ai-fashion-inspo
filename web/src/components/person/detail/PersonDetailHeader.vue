<script setup lang="ts">
/** 人物详情头部信息卡：头像（手动设置，未设置显示首字）、名称 + 内容类型徽标、元信息、主页链接与操作。 */

import { ref } from 'vue'
import { getFileUrl } from '@/api/inspirations'
import { PERSON_PLATFORM_LABELS, type PersonDetail, type PersonType } from '@shared/types/person'
import PersonTypeTag from '@/components/person/PersonTypeTag.vue'
import PersonAvatarPicker from '@/components/person/PersonAvatarPicker.vue'

const props = defineProps<{
  detail: PersonDetail
  kind: PersonType
  kindLabel: string
  profileUrlSafe: boolean
  /** 「下载所有作品」不可用（非抖音博主 / 没主页链接且无法自动解析 / f2 通道未就绪） */
  worksDownloadDisabled: boolean
  /** 不可用原因（hover 提示；可用时为空串） */
  worksDownloadReason: string
  /** 缺主页链接但可自动解析（抖音「我的喜欢」采回来的博主常见）：按钮改文案、确认框说明会先解析 */
  worksDownloadNeedsResolve?: boolean
  /** 正在自动解析抖音主页（联网反查 20~40 秒）：按钮转圈防重复点击 */
  worksDownloadResolving?: boolean
}>()

defineEmits<{
  edit: []
  delete: []
  /** 点击简介区域的「AI 生成」：由详情页打开编辑弹窗并自动生成简介 */
  'generate-bio': []
  /** 头像更新/清除后：由详情页重新拉取详情 */
  'avatar-changed': []
  /** 下载该博主的全部作品：由详情页提交 f2 任务 */
  'download-works': []
}>()

/** 头像选择弹窗开关（仅穿搭博主提供手动设置入口） */
const avatarPickerVisible = ref(false)

/** 头像地址：只有手动设置一条来源（从 TA 的素材选一张或本地上传）；未设置则显示首字 */
function avatarSrc(): string {
  return props.detail.avatar_path ? getFileUrl(props.detail.avatar_path) : ''
}
</script>

<template>
  <a-card size="small" class="header-card">
    <div class="header-row">
      <div class="avatar-wrap">
        <!-- 头像只有手动设置一条来源；未设置时显示名字首字 -->
        <img v-if="avatarSrc()" :src="avatarSrc()" class="avatar-img" :alt="detail.name" />
        <span v-else class="avatar-fallback">{{ detail.name.slice(0, 1) }}</span>
      </div>

      <div class="header-info">
        <div class="name-line">
          <h2 style="margin: 0">{{ detail.name }}</h2>
          <!-- 内容类型徽标：UI 区分核心 -->
          <PersonTypeTag :type="kind" size="medium" />
        </div>
        <div class="meta-line">
          <a-tag size="small">
            {{ PERSON_PLATFORM_LABELS[detail.platform] || detail.platform }}
          </a-tag>
          <a-typography-text type="secondary" style="font-size: 13px">
            {{ detail.inspiration_count ?? 0 }} 条素材 · 创建于
            {{ detail.created_at ? new Date(detail.created_at).toLocaleDateString('zh-CN') : '-' }}
          </a-typography-text>
        </div>
        <!-- 简介：有内容时以独立块展示（保留换行），无内容时给轻量占位，避免头像下方信息断裂 -->
        <div class="bio-block">
          <span class="bio-label">简介</span>
          <a-typography-text v-if="detail.bio" class="bio-text" type="secondary">
            <span class="bio-text-inner">{{ detail.bio }}</span>
          </a-typography-text>
          <a-typography-text v-else class="bio-text bio-empty" type="secondary">
            暂无简介，点击右上角「编辑」补充
          </a-typography-text>
          <a-button type="text" size="mini" class="bio-generate" @click="$emit('generate-bio')">
            ✨ AI 生成
          </a-button>
        </div>
        <div v-if="detail.profile_url" class="bio-line">
          <a
            v-if="profileUrlSafe"
            :href="detail.profile_url"
            target="_blank"
            rel="noopener noreferrer"
            >主页链接 ↗</a
          >
          <a-typography-text v-else type="secondary"
            >主页链接：{{ detail.profile_url }}</a-typography-text
          >
        </div>
      </div>

      <div class="header-actions">
        <!-- 下载该博主的全部作品（仅穿搭博主）：点名她的主页链接，让 f2 翻全量下载并
             入库；已下载过的作品自动跳过，首次会较久（进度在任务中心）。
             没主页链接但能自动解析时（素材来自「我的喜欢 / 我的收藏」）按钮改用「解析主页
             并下载」文案并说明要等一次联网反查；否则禁用 + hover 说明原因。 -->
        <template v-if="kind === 'blogger'">
          <a-popconfirm
            v-if="!worksDownloadDisabled"
            :content="
              worksDownloadNeedsResolve
                ? '该博主还没有主页链接：将先用 TA 的素材作品自动解析抖音主页（需联网，约 20~40 秒），解析成功后再下载全部作品。确定开始？'
                : '将下载该博主主页的全部作品并入库（首次翻全量，之后只下新增；已下载过的自动跳过）。首次可能较久，确定开始？'
            "
            :ok-button-props="{ loading: worksDownloadResolving }"
            @ok="$emit('download-works')"
          >
            <a-button type="secondary" :loading="worksDownloadResolving">
              {{ worksDownloadNeedsResolve ? '解析主页并下载全部作品' : '下载所有作品' }}
            </a-button>
          </a-popconfirm>
          <a-tooltip v-else :content="worksDownloadReason">
            <a-button type="secondary" disabled>下载所有作品</a-button>
          </a-tooltip>
        </template>
        <!-- 手动设置头像（仅穿搭博主）：从 TA 的素材里选一张，或上传一张照片 -->
        <a-button v-if="kind === 'blogger'" type="secondary" @click="avatarPickerVisible = true">
          {{ detail.avatar_path ? '更换头像' : '上传头像' }}
        </a-button>
        <a-button type="secondary" @click="$emit('edit')">编辑</a-button>
        <a-popconfirm
          :content="`确定删除${kindLabel}「${detail.name}」？仅当该人物无关联素材时才可删除。`"
          @ok="$emit('delete')"
        >
          <a-button type="secondary" status="danger">删除</a-button>
        </a-popconfirm>
      </div>
    </div>

    <PersonAvatarPicker
      v-if="kind === 'blogger'"
      v-model:visible="avatarPickerVisible"
      :person-id="detail.id"
      :person-name="detail.name"
      :has-avatar="Boolean(detail.avatar_path)"
      @saved="$emit('avatar-changed')"
    />
  </a-card>
</template>

<style scoped>
.header-card {
  margin-bottom: 12px;
}

.header-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.avatar-wrap {
  width: 72px;
  height: 72px;
  flex-shrink: 0;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eef1f6;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-fallback {
  font-size: 30px;
  color: #4a5a7a;
  font-weight: 600;
}

.header-info {
  flex: 1;
  min-width: 0;
}

.name-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.bio-line {
  margin-top: 4px;
}

/* 简介块：独立卡片感的浅色块，保留换行、长文折叠 */
.bio-block {
  margin-top: 8px;
  padding: 8px 12px;
  background: var(--color-fill-1, #f7f8fa);
  border-radius: 6px;
  border-left: 3px solid var(--color-text-4, #c9cdd4);
}

.bio-label {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-2, #4e5969);
  margin-right: 8px;
  vertical-align: top;
}

.bio-text {
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
}

.bio-text-inner {
  white-space: pre-wrap;
}

.bio-empty {
  font-style: italic;
  opacity: 0.7;
}

/* 简介块内的「AI 生成」按钮：与标签同行、右对齐，不挤占正文行 */
.bio-generate {
  float: right;
  margin-top: 2px;
  color: var(--color-primary-6, #165dff);
}

.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
