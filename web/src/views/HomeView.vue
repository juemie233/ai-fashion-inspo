<script setup lang="ts">
/** 首页：瀑布流展示最近素材，支持收藏筛选和无限滚动。 */

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { useInspirationsStore } from '@/stores/inspirations'

const router = useRouter()
const message = useMessage()
const store = useInspirationsStore()

/** 是否仅显示收藏 */
const onlyFavorites = ref(false)

onMounted(() => {
  store.load()
})

/** 加载更多（无限滚动触发） */
function loadMore() {
  store.loadMore()
}

/** 切换收藏筛选 */
function toggleFavoritesFilter() {
  onlyFavorites.value = !onlyFavorites.value
  store.load({
    is_favorite: onlyFavorites.value ? true : undefined,
    page: 1,
  })
}

/** 删除素材 */
async function handleDelete(id: string) {
  try {
    await store.remove(id)
    message.success('已删除')
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
</script>

<template>
  <div class="home-page">
    <!-- 顶部操作栏 -->
    <div class="page-header">
      <div class="header-left">
        <h2>灵感库</h2>
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
      :has-more="store.items.length < store.total"
      @load-more="loadMore"
      @delete="handleDelete"
      @toggle-favorite="handleToggleFavorite"
    />
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
</style>
