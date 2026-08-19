<script setup lang="ts">
/** 素材管理页（日常操作）：概览、疑似 AI 素材、手机图剪裁、垃圾桶。
 *
 * 治理类功能（批量清理/数据完整性/重复文件/近似重复）拆分至「数据治理」页，
 * 报表类（向量管理/数据报表）拆分至「数据洞察」页。 */

import { ref, onMounted, watch } from 'vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { useRouter, useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { batchTrash } from '@/api/inspirations'
import type { Stats, LargeFile } from '@/types/admin'

import AdminServiceHealth from '@/components/admin/AdminServiceHealth.vue'
import AdminStatCards from '@/components/admin/AdminStatCards.vue'
import AdminDistStats from '@/components/admin/AdminDistStats.vue'
import AdminLargeFiles from '@/components/admin/AdminLargeFiles.vue'
import AdminAiReview from '@/components/admin/AdminAiReview.vue'
import AdminPhoneCrop from '@/components/admin/AdminPhoneCrop.vue'
import AdminTrash from '@/components/admin/AdminTrash.vue'

const router = useRouter()
const route = useRoute()

// ── 子页面（小菜单）状态 ──
type AdminTab = 'overview' | 'ai' | 'crop' | 'trash'
const ADMIN_TABS: AdminTab[] = ['overview', 'ai', 'crop', 'trash']

/** 从 URL query 恢复上次停留的子页面：刷新页面后仍停留在原小页面而非回到「概览」 */
function initialTab(): AdminTab {
  const t = route.query.tab
  return t && ADMIN_TABS.includes(t as AdminTab) ? (t as AdminTab) : 'overview'
}
const activeTab = ref<AdminTab>(initialTab())

// 切换子页面时同步到 URL query（replace 不产生历史记录，刷新后可恢复）
watch(activeTab, (tab) => {
  const query = { ...route.query }
  if (tab === 'overview') {
    delete query.tab
  } else {
    query.tab = tab
  }
  router.replace({ query })
})

/** 疑似 AI 子页面刷新键：批量移入垃圾桶完成后自增，通知子页面重新加载 */
const aiRefreshKey = ref(0)

// ── 响应式状态 ──

const stats = ref<Stats | null>(null)
const largestFiles = ref<LargeFile[]>([])
const loading = ref(true)

// ── 疑似 AI 素材移入垃圾桶（软删除，可恢复）：来源标记自动移动，原因「AI生成」 ──

const aiTrashing = ref(false)

async function batchDeleteByIds(ids: string[]) {
  aiTrashing.value = true
  try {
    const { trashed, skipped } = await batchTrash(ids, 'AI生成', 'auto')
    const parts = [`已将 ${trashed} 个疑似 AI 素材移入垃圾桶`]
    if (skipped > 0) parts.push(`${skipped} 个跳过（不存在或已在垃圾桶）`)
    Message.success(parts.join('，'))
    aiRefreshKey.value += 1 // 通知疑似 AI 子页面刷新
    loadAll()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '移入垃圾桶失败'))
  } finally {
    aiTrashing.value = false
  }
}

// ── 数据加载 ──

async function loadAll() {
  loading.value = true
  try {
    const [sRes, lRes] = await Promise.all([
      apiClient.get('/admin/stats'),
      apiClient.get('/admin/largest-files?limit=20'),
    ])
    stats.value = sRes.data
    largestFiles.value = lRes.data
  } catch {
    Message.error('加载统计数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAll()
})
</script>

<template>
  <div class="admin-page">
    <h2>素材管理</h2>
    <p class="subtitle">概览、剪裁与垃圾桶（治理类操作见「数据治理」「数据洞察」）</p>

    <!-- ====== 子页面小菜单 ====== -->
    <a-tabs v-model:active-key="activeTab" type="line">
      <!-- 概览 -->
      <a-tab-pane key="overview" title="概览">
        <admin-service-health />
        <admin-stat-cards :stats="stats" />
        <admin-dist-stats :stats="stats" />
        <admin-large-files :files="largestFiles" />
        <p style="color: #999; font-size: 12px">
          💡 提示：定期检查数据完整性和重复文件，可以保持素材库健康。建议每月执行一次。
        </p>
      </a-tab-pane>

      <!-- 疑似 AI 素材（与侧边栏「AI 模型」区分，避免混淆） -->
      <a-tab-pane key="ai" title="疑似 AI 素材">
        <admin-ai-review
          :refresh-key="aiRefreshKey"
          :deleting="aiTrashing"
          @delete-selected="batchDeleteByIds"
        />
      </a-tab-pane>

      <!-- 手机图剪裁 -->
      <a-tab-pane key="crop" title="手机图剪裁">
        <admin-phone-crop />
      </a-tab-pane>

      <!-- 垃圾桶 -->
      <a-tab-pane key="trash" title="垃圾桶">
        <admin-trash />
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.admin-page {
  max-width: 1100px;
  margin: 0 auto;
}
.subtitle {
  color: #999;
  margin-bottom: 24px;
}
</style>
