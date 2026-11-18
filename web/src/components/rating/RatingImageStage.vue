<script setup lang="ts">
/** 评分页公共「图片舞台」：大图 + 信息行（来源/作者/时间）+ 标签。
 *
 * 首页随机评分与人物优先评分共用，避免两页各写一份大图/信息区模板。
 * 只负责展示：加载态、图片地址、信息文本与标签名由调用方传入。
 */

withDefaults(
  defineProps<{
    /** 大图地址（原图，评分看细节；调用方用 getFileUrl 拼好） */
    src: string
    /** 图片 alt（无作者时用默认文案） */
    alt?: string
    /** 图片加载中（a-spin 覆盖舞台） */
    loading?: boolean
    /** 图片信息摘要（来源 · 作者 · 入库时间 · 审核状态等） */
    metaText?: string
    /** 标签名（调用方自行裁剪数量） */
    tags?: string[]
  }>(),
  {
    alt: '待评分素材',
    loading: false,
    metaText: '',
    tags: () => [],
  },
)
</script>

<template>
  <a-spin :loading="loading" class="rating-image-spin">
    <div class="rate-stage">
      <img :src="src" :alt="alt" class="rate-image" />
    </div>
  </a-spin>

  <div v-if="metaText || tags.length" class="rate-info">
    <div class="rate-meta">{{ metaText }}</div>
    <div v-if="tags.length" class="rate-tags">
      <a-tag v-for="tag in tags" :key="tag" size="small">{{ tag }}</a-tag>
    </div>
  </div>
</template>

<style scoped>
/* 印样台：舞台是纸质样张的装裱面，大图不加圆角 */
.rating-image-spin {
  display: block;
}

.rate-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  max-height: 68vh;
  overflow: hidden;
  background: var(--sheet-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.rate-image {
  display: block;
  max-width: 100%;
  max-height: 68vh;
  object-fit: contain;
  border-radius: 0;
}

.rate-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}

.rate-meta {
  color: var(--ink-2);
  font-size: 13px;
}

.rate-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
