<script setup lang="ts">
/** 高级搜索页：关键词搜索 + 多维标签筛选 + 高级筛选 + 相似推荐 + 搜索历史。 */

import { h, ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import SearchBar from '@/components/search/SearchBar.vue'
import TagFilter from '@/components/search/TagFilter.vue'
import MasonryGrid from '@/components/inspiration/MasonryGrid.vue'
import { useTagsStore } from '@/stores/tags'
import { useInspirationsStore } from '@/stores/inspirations'
import { searchInspirations, type SearchQuery } from '@/api/search'
import { getFileUrl } from '@/api/inspirations'
import type { InspirationOut } from '@/api/inspirations'

const router = useRouter()
const route = useRoute()
const message = useMessage()
const tagsStore = useTagsStore()
const inspStore = useInspirationsStore()

// ── 响应式状态 ──

const results = ref<InspirationOut[]>([])
const total = ref(0)
const searching = ref(false)
const filterVisible = ref(true)
const showMoreFilters = ref(false)

// 搜索参数
const keyword = ref((route.query.q as string) || '')
const currentPage = ref(1)
const pageSize = ref(50)
const sortMode = ref((route.query.sort as string) || 'newest')
const sourceFilter = ref((route.query.source as string) || '')
const mediaFilter = ref((route.query.media as string) || '')
const analysisFilter = ref((route.query.analysis as string) || '')
const dateFrom = ref((route.query.from as string) || '')
const dateTo = ref((route.query.to as string) || '')

// 密度
const density = ref<'compact' | 'standard' | 'comfortable'>(
  (localStorage.getItem('search-density') as 'compact' | 'standard' | 'comfortable') || 'compact'
)

// 搜索历史
const searchHistory = ref<string[]>(
  JSON.parse(localStorage.getItem('search-history') || '[]')
)

const totalPages = computed(() => Math.ceil(total.value / pageSize.value))

// ── URL 同步 ──

function syncUrl() {
  const query: Record<string, string> = {}
  if (keyword.value) query.q = keyword.value
  if (sortMode.value !== 'newest') query.sort = sortMode.value
  if (sourceFilter.value) query.source = sourceFilter.value
  if (mediaFilter.value) query.media = mediaFilter.value
  if (analysisFilter.value) query.analysis = analysisFilter.value
  if (dateFrom.value) query.from = dateFrom.value
  if (dateTo.value) query.to = dateTo.value
  router.replace({ query })
}

// ── 搜索执行 ──

function buildQuery(page: number): SearchQuery {
  const query: SearchQuery = {
    combine: tagsStore.combineMode,
    page,
    size: pageSize.value,
    sort: sortMode.value,
  }

  const includedTags = [...tagsStore.selectedTags]
  if (includedTags.length > 0) query.include_tags = includedTags.join(',')
  if (tagsStore.excludedTags.size > 0) query.exclude_tags = [...tagsStore.excludedTags].join(',')
  if (keyword.value) query.keyword = keyword.value
  if (sourceFilter.value) query.source_type = sourceFilter.value
  if (mediaFilter.value) query.media_type = mediaFilter.value
  if (analysisFilter.value) query.analysis_status = analysisFilter.value
  if (dateFrom.value) query.date_from = dateFrom.value
  if (dateTo.value) query.date_to = dateTo.value

  return query
}

async function doSearch(page: number = 1) {
  searching.value = true
  currentPage.value = page
  try {
    const data = await searchInspirations(buildQuery(page))
    results.value = data.items
    total.value = data.total
    syncUrl()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch {
    message.error('搜索失败')
  } finally {
    searching.value = false
  }
}

// ── 搜索栏回调 ──

function handleSearchBar(val: string) {
  // 颜色代码 → 直接搜索
  if (/^#[0-9a-fA-F]{6}$/.test(val)) {
    keyword.value = ''
    doSearch(1)
    // 颜色搜索通过 URL 参数处理，这里简化：直接把颜色作为关键词
    keyword.value = val
    doSearch(1)
    return
  }
  // 普通搜索
  keyword.value = val
  addToHistory(val)
  doSearch(1)
}

function addToHistory(val: string) {
  const h = searchHistory.value.filter(h => h !== val)
  h.unshift(val)
  searchHistory.value = h.slice(0, 10)
  localStorage.setItem('search-history', JSON.stringify(searchHistory.value))
}

// ── 标签筛选变化时自动搜索 ──

watch(
  () => [tagsStore.selectedTags, tagsStore.excludedTags, tagsStore.combineMode] as const,
  () => {
    if (tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0) {
      doSearch(1)
    } else if (!keyword.value) {
      // 清除所有筛选后重新加载
      doSearch(1)
    }
  },
  { deep: false }
)

// ── 排序/筛选变更 ──

function onSortChange() { doSearch(1) }
function onFilterChange() { doSearch(1) }

function setDensity(d: 'compact' | 'standard' | 'comfortable') {
  density.value = d
  localStorage.setItem('search-density', d)
}

// ── 删除/收藏 ──

async function handleDelete(id: string) {
  try {
    await inspStore.remove(id)
    results.value = results.value.filter(r => r.id !== id)
    total.value--
    message.success('已删除')
  } catch {
    message.error('删除失败')
  }
}

async function handleToggleFavorite(id: string) {
  try {
    await inspStore.toggleFavorite(id)
  } catch {
    message.error('操作失败')
  }
}

// ── 搜索历史应用 ──

function applyHistory(q: string) {
  keyword.value = q
  doSearch(1)
}

function clearHistory() {
  searchHistory.value = []
  localStorage.removeItem('search-history')
}

// ── 排序选项 ──

const sortOptions = [
  { label: '最新在前', value: 'newest' },
  { label: '最旧在前', value: 'oldest' },
  { label: '标签最多', value: 'tag_count' },
  { label: '匹配优先', value: 'match_score' },
]

// 初始加载
onMounted(() => {
  tagsStore.load()
  if (keyword.value || tagsStore.selectedTags.size > 0 || tagsStore.excludedTags.size > 0) {
    doSearch(1)
  } else {
    doSearch(1)
  }
})
</script>

<template>
  <div class="search-page">
    <h2>高级搜索</h2>

    <!-- 搜索栏 -->
    <div class="search-section">
      <SearchBar @search="handleSearchBar" />
    </div>

    <!-- 搜索历史 -->
    <div v-if="searchHistory.length > 0 && !keyword" class="search-history">
      <span style="font-size:12px;color:#999">最近搜索：</span>
      <n-tag
        v-for="(h, i) in searchHistory.slice(0, 6)"
        :key="i"
        size="tiny"
        style="cursor:pointer"
        @click="applyHistory(h)"
      >
        {{ h }}
      </n-tag>
      <n-button size="tiny" text @click="clearHistory" style="font-size:11px">清除历史</n-button>
    </div>

    <!-- 当前筛选信息 -->
    <div v-if="keyword || tagsStore.selectedTags.size > 0" class="search-context">
      搜索 <strong v-if="keyword">"{{ keyword }}"</strong>
      <span v-if="tagsStore.selectedTags.size > 0">
        {{ keyword ? ' · ' : '' }}{{ tagsStore.combineMode === 'AND' ? '全部匹配' : '任意匹配' }}：
        <n-tag v-for="n in [...tagsStore.selectedTags]" :key="n" size="tiny" type="info">{{ n }}</n-tag>
      </span>
      <span v-if="tagsStore.excludedTags.size > 0">
        · 排除：<n-tag v-for="n in [...tagsStore.excludedTags]" :key="n" size="tiny" type="error">{{ n }}</n-tag>
      </span>
    </div>

    <!-- 标签筛选 + 结果 -->
    <div class="search-layout">
      <!-- 左侧筛选面板 -->
      <transition name="slide">
        <aside v-if="filterVisible" class="filter-panel">
          <n-card title="标签筛选" size="small" :bordered="true">
            <template #header-extra>
              <n-button size="tiny" text @click="filterVisible = false">收起 ✕</n-button>
            </template>
            <TagFilter />

            <!-- 更多筛选（可折叠） -->
            <n-collapse style="margin-top:8px">
              <n-collapse-item title="更多筛选" name="more">
                <div class="more-filters">
                  <div class="filter-row">
                    <label>来源</label>
                    <n-select
                      v-model:value="sourceFilter"
                      :options="[
                        {label:'全部',value:''},{label:'手动上传',value:'manual_upload'},
                        {label:'自动采集',value:'scraper'},{label:'小红书',value:'xiaohongshu'},
                        {label:'浏览器插件',value:'browser_extension'}
                      ]"
                      size="tiny"
                      clearable
                      @update:value="onFilterChange"
                    />
                  </div>
                  <div class="filter-row">
                    <label>媒体</label>
                    <n-select
                      v-model:value="mediaFilter"
                      :options="[{label:'全部',value:''},{label:'图片',value:'image'},{label:'视频',value:'video'}]"
                      size="tiny"
                      @update:value="onFilterChange"
                    />
                  </div>
                  <div class="filter-row">
                    <label>分析状态</label>
                    <n-select
                      v-model:value="analysisFilter"
                      :options="[{label:'全部',value:''},{label:'已分析',value:'done'},{label:'未分析',value:'pending'},{label:'分析失败',value:'error'}]"
                      size="tiny"
                      @update:value="onFilterChange"
                    />
                  </div>
                  <div class="filter-row">
                    <label>开始日期</label>
                    <n-input v-model:value="dateFrom" type="date" size="tiny" @change="onFilterChange" />
                  </div>
                  <div class="filter-row">
                    <label>结束日期</label>
                    <n-input v-model:value="dateTo" type="date" size="tiny" @change="onFilterChange" />
                  </div>
                </div>
              </n-collapse-item>
            </n-collapse>

            <n-button
              type="primary"
              block
              style="margin-top:8px"
              @click="doSearch(1)"
              :loading="searching"
            >
              搜索
            </n-button>
          </n-card>
        </aside>
      </transition>

      <!-- 右侧结果 -->
      <main class="result-panel">
        <n-card size="small" :bordered="true">
          <template #header>
            <div class="result-header-row">
              <span v-if="!filterVisible">
                <n-button size="tiny" type="primary" secondary @click="filterVisible = true">
                  展开筛选
                </n-button>
              </span>
              <span v-if="total > 0" class="result-count">
                找到 <strong>{{ total }}</strong> 条结果
              </span>
              <span v-else-if="!searching" style="color:#999">
                {{ keyword || tagsStore.selectedTags.size > 0
                  ? '未找到匹配结果，请尝试放宽筛选条件'
                  : '输入关键词或选择标签开始搜索' }}
              </span>
              <span style="flex:1" />
              <!-- 排序 + 密度 -->
              <n-select
                v-model:value="sortMode"
                :options="sortOptions"
                size="tiny"
                style="width:110px"
                @update:value="onSortChange"
              />
              <n-button-group size="tiny">
                <n-button :type="density==='compact'?'primary':'default'" @click="setDensity('compact')" title="紧凑">⊞</n-button>
                <n-button :type="density==='standard'?'primary':'default'" @click="setDensity('standard')" title="标准">⊟</n-button>
                <n-button :type="density==='comfortable'?'primary':'default'" @click="setDensity('comfortable')" title="宽松">⊠</n-button>
              </n-button-group>
            </div>
          </template>

          <!-- 无结果诊断 -->
          <div v-if="total === 0 && !searching && (tagsStore.selectedTags.size > 0 || keyword)" class="no-result-hint">
            <n-alert type="info" style="margin-bottom:12px">
              <template #header>未找到匹配结果，建议尝试：</template>
              <ul style="margin:4px 0;padding-left:16px;font-size:12px">
                <li v-if="tagsStore.combineMode === 'AND' && tagsStore.selectedTags.size > 1">
                  将「全部匹配」切换为「任意匹配」模式
                </li>
                <li v-if="tagsStore.selectedTags.size > 1">减少已选标签数量</li>
                <li v-if="tagsStore.excludedTags.size > 0">减少排除标签</li>
                <li>检查关键词拼写或尝试更宽泛的词语</li>
              </ul>
            </n-alert>
          </div>

          <MasonryGrid
            :items="results"
            :loading="searching"
            :density="density"
            @delete="handleDelete"
            @toggle-favorite="handleToggleFavorite"
          />
        </n-card>

        <!-- 分页 -->
        <div v-if="totalPages > 1" class="pagination-wrapper">
          <n-pagination
            v-model:page="currentPage"
            :page-count="totalPages"
            :page-size="pageSize"
            show-size-picker
            :page-sizes="[25, 50, 100]"
            @update:page="doSearch"
            @update:page-size="(s: number) => { pageSize = s; doSearch(1) }"
          />
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.search-page {
  max-width: 1800px;
  margin: 0 auto;
}

.search-section {
  margin-bottom: 8px;
}

.search-history {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.search-context {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.search-layout {
  display: flex;
  gap: 16px;
}

.filter-panel {
  width: 290px;
  flex-shrink: 0;
}

.result-panel {
  flex: 1;
  min-width: 0;
}

.result-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.result-count {
  color: #666;
  font-size: 14px;
}

/* 更多筛选 */
.more-filters .filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.more-filters label {
  font-size: 12px;
  color: #666;
  width: 60px;
  flex-shrink: 0;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

/* 折叠动画 */
.slide-enter-active, .slide-leave-active {
  transition: width 0.25s ease, opacity 0.2s ease;
  overflow: hidden;
}
.slide-enter-from, .slide-leave-to {
  width: 0;
  opacity: 0;
}

@media (max-width: 900px) {
  .search-layout { flex-direction: column; }
  .filter-panel { width: 100%; }
}
</style>
