<script setup lang="ts">
/** 人物详情头部信息卡：头像（人脸小图→手动头像→首字）、名称 + 内容类型徽标、元信息、主页链接与操作。 */

import { getFileUrl } from '@/api/inspirations'
import { PERSON_PLATFORM_LABELS, type PersonDetail, type PersonType } from '@shared/types/person'
import PersonTypeTag from '@/components/person/PersonTypeTag.vue'

defineProps<{
  detail: PersonDetail
  kind: PersonType
  kindLabel: string
  profileUrlSafe: boolean
}>()

defineEmits<{
  edit: []
  delete: []
}>()
</script>

<template>
  <a-card size="small" class="header-card">
    <div class="header-row">
      <div class="avatar-wrap">
        <!-- 展示优先级：人脸小图（自动裁剪）→ 手动头像 → 名字首字 -->
        <img
          v-if="detail.face_thumb_path || detail.avatar_path"
          :src="getFileUrl(detail.face_thumb_path || (detail.avatar_path as string))"
          class="avatar-img"
          :alt="detail.name"
        />
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
        <div v-if="detail.bio" class="bio-line">
          <a-typography-text type="secondary">{{ detail.bio }}</a-typography-text>
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
        <a-button type="secondary" @click="$emit('edit')">编辑</a-button>
        <a-popconfirm
          :content="`确定删除${kindLabel}「${detail.name}」？仅当该人物无关联素材时才可删除。`"
          @ok="$emit('delete')"
        >
          <a-button type="secondary" status="danger">删除</a-button>
        </a-popconfirm>
      </div>
    </div>
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

.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
