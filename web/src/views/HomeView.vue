<script setup lang="ts">
/** 首页：瀑布流展示素材，支持分页和收藏筛选。 */

import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { useInspirationsStore } from '@/stores/inspirations'

const router = useRouter()
const message = useMessage()
const store = useInspirationsStore()

/** 当前页码 */
const currentPage = ref(1)
/** 每页数量 */
const pageSize = ref(50)
/** 是否仅显示收藏 */
const onlyFavorites = ref(false)
/** 总页数 */
const totalPages = computed(() => Math.ceil(store.total / pageSize.value))

/** 加载指定页 */
function loadPage(page: number) {
  currentPage.value = page
  store.load({
    page,
    size: pageSize.value,
    is_favorite: onlyFavorites.value ? true : undefined,
  })
  // 滚动到顶部
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

/** 切换收藏筛选 */
function toggleFavoritesFilter() {
  onlyFavorites.value = !onlyFavorites.value
  currentPage.value = 1
  store.load({
    page: 1,
    is_favorite: onlyFavorites.value ? true : undefined,
  })
}

/** 删除素材 */
async function handleDelete(id: string) {
  try {
    await store.remove(id)
    message.success('已删除')
    // 如果当前页空了且不是第一页，回退一页
    if (store.items.length === 0 && currentPage.value > 1) {
      loadPage(currentPage.value - 1)
    }
  } catch {
    message.error('删除失败')
  }
}

/** 切换收藏 */
async function handleToggleFavorite(id: string) {
  try {
    await store.toggleFavorite(id)
  } catch {
    message.error('操作失败')
  }
}

// 初始加载
loadPage(1)
</script>

<template>
  <div class="home-page">
    <!-- 顶部操作栏 -->
    <div class="page-header">
      <div class="header-left">
        <h2>素材库</h2>
        <span class="total-count">共 {{ store.total }} 条素材</span>
      </div>
      <div class="header-right">
        <n-button
          :type="onlyFavorites ? 'warning' : 'default'"
          @click="toggleFavoritesFilter"
        >
          {{ onlyFavorites ? '⭐ 仅显示收藏' : '全部素材' }}
        </n-button>
        <n-button type="primary" @click="router.push('/upload')">
          上传素材
        </n-button>
      </div>
    </div>

    <!-- 瀑布流 -->
    <MasonryGrid
      :items="store.items"
      :loading="store.loading"
      @delete="handleDelete"
      @toggle-favorite="handleToggleFavorite"
    />

    <!-- 底部分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <n-pagination
        v-model:page="currentPage"
        :page-count="totalPages"
        :page-size="pageSize"
        show-size-picker
        :page-sizes="[25, 50, 100]"
        @update:page="loadPage"
        @update:page-size="(s: number) => { pageSize = s; loadPage(1) }"
      />
    </div>
  </div>
</template>

<style scoped>
.home-page {
  max-width: 1600px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-left h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
}

.total-count {
  font-size: 14px;
  color: #999;
}

.header-right {
  display: flex;
  gap: 8px;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
