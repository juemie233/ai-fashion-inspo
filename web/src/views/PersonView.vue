<script setup lang="ts">
/** 人物管理页：穿搭博主 / 职业模特 双 Tab（两类已物理拆分为独立表与 API）。
 *
 * 当前 Tab 同步到 URL query（?kind=blogger|model），刷新后保持；
 * 各 Tab 内的页码/搜索/筛选由 PersonListSection 持久化到同一 URL。
 */

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { PersonKind } from '@/stores/persons'
import PersonListSection from '@/components/person/PersonListSection.vue'

const route = useRoute()
const router = useRouter()

/** 当前 Tab：初始从 URL 恢复（刷新/详情返回后保持），缺省穿搭博主 */
const activeKind = ref<PersonKind>(route.query.kind === 'model' ? 'model' : 'blogger')

// Tab 切换时同步 URL（保留其他列表上下文参数）
watch(activeKind, (kind) => {
  router.replace({ path: '/persons', query: { ...route.query, kind } })
})
</script>

<template>
  <div class="person-page">
    <div class="page-header">
      <div>
        <h2>人物管理</h2>
        <a-typography-text type="secondary" style="font-size: 13px">
          穿搭博主与职业模特已独立管理：博主（平台主页/小红书号/CSV 导入）与模特（写真照片组）业务逻辑分别演进
        </a-typography-text>
      </div>
      <a-button type="primary" @click="router.push('/face-scan')">人脸库扫描</a-button>
    </div>

    <a-tabs v-model:active-key="activeKind" type="line">
      <a-tab-pane key="blogger" title="穿搭博主">
        <PersonListSection v-if="activeKind === 'blogger'" kind="blogger" />
      </a-tab-pane>
      <a-tab-pane key="model" title="职业模特">
        <PersonListSection v-if="activeKind === 'model'" kind="model" />
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.person-page {
  max-width: 1100px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
}
</style>
