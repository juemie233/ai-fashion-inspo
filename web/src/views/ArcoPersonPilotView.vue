<script setup lang="ts">
/** Arco Design 试点页：用 @arco-design/web-vue 重做穿搭博主列表。
 *
 * 目的：与 Naive UI 版（/persons）并存对比，验证 Arco 组件、主题定制
 * （设计令牌 CSS 变量）与本项目的适配度；效果满意后再决定是否全量迁移。
 * 数据接口完全复用现有 /api/bloggers，不依赖任何 Naive UI 组件。
 */

import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Avatar, Message, type TableColumnData } from '@arco-design/web-vue'
import { bloggersApi, type PersonIpStats } from '@/api/persons'
import { getFileUrl } from '@/api/inspirations'
import type { Person } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'

const router = useRouter()

// ── 列表状态（简单版：不持久化 URL，聚焦组件效果对比）──

const loading = ref(false)
const items = ref<Person[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const search = ref('')
const platform = ref('')
const sort = ref('count')

const platformOptions = [
  { label: '全部平台', value: '' },
  ...Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value })),
]
const sortOptions = [
  { label: '素材数最多', value: 'count' },
  { label: '最新创建', value: 'newest' },
  { label: '名称排序', value: 'name' },
]

/** 加载博主列表（复用现有 API） */
async function load() {
  loading.value = true
  try {
    const data = await bloggersApi.fetchList({
      page: page.value,
      size: pageSize.value,
      search: search.value.trim() || undefined,
      platform: platform.value || undefined,
      sort: sort.value as 'count' | 'newest' | 'name',
    })
    items.value = data.items
    total.value = data.total
  } catch {
    Message.error('加载博主列表失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  load()
}

function goDetail(row: Person) {
  router.push({ path: `/persons/${row.id}`, query: { kind: 'blogger' } })
}

// ── 热门排行（Arco 卡片）──

const topPersons = ref<Person[]>([])

async function loadTop() {
  try {
    topPersons.value = await bloggersApi.fetchTop(5)
  } catch {
    // 排行加载失败不阻塞主列表
  }
}

// ── IP 属地统计数字（Arco 数据展示，替代 ECharts 柱状图做轻量对比）──

const ipStats = ref<PersonIpStats | null>(null)

async function loadIpStats() {
  try {
    ipStats.value = await bloggersApi.fetchIpStats(30)
  } catch {
    // 统计加载失败不阻塞列表
  }
}

// ── Arco 表格列定义（render 用 h() 组合节点）──

function renderAvatarCell(record: Person) {
  return h('div', { class: 'pilot-person-cell' }, [
    h(
      Avatar,
      { size: 40 },
      {
        default: () =>
          record.face_thumb_path || record.avatar_path
            ? h('img', {
                src: getFileUrl(record.face_thumb_path || (record.avatar_path as string)),
                alt: record.name,
              })
            : h('svg', { viewBox: '0 0 24 24', class: 'pilot-avatar-icon', 'aria-hidden': 'true' }, [
                h('path', {
                  d: 'M12 12c2.7 0 4.9-2.2 4.9-4.9S14.7 2.2 12 2.2 7.1 4.4 7.1 7.1 9.3 12 12 12zm0 2.4c-3.4 0-10.1 1.7-10.1 5v2.4h20.2v-2.4c0-3.3-6.7-5-10.1-5z',
                }),
              ]),
      },
    ),
    h('span', { class: 'pilot-person-name' }, record.name),
  ])
}

/** Arco 表格 render 的 record 转 Person（TableData 是宽松键值类型） */
function toPerson(record: unknown): Person {
  return record as Person
}

const columns: TableColumnData[] = [
  {
    title: '人物',
    dataIndex: 'name',
    width: 200,
    render: ({ record }) => renderAvatarCell(toPerson(record)),
  },
  {
    title: '平台',
    dataIndex: 'platform',
    width: 90,
    render: ({ record }) => PERSON_PLATFORM_LABELS[toPerson(record).platform] || toPerson(record).platform,
  },
  {
    title: '小红书ID',
    dataIndex: 'xhs_id',
    width: 130,
    render: ({ record }) => toPerson(record).xhs_id || '-',
  },
  {
    title: 'IP属地',
    dataIndex: 'ip_location',
    width: 90,
    render: ({ record }) => toPerson(record).ip_location || '-',
  },
  {
    title: '素材数',
    dataIndex: 'inspiration_count',
    width: 80,
    render: ({ record }) => String(toPerson(record).inspiration_count ?? 0),
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    width: 160,
    render: ({ record }) => {
      const created = toPerson(record).created_at
      return created ? new Date(created).toLocaleString('zh-CN') : '-'
    },
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 90,
    render: ({ record }) =>
      h('a-button', { size: 'small', type: 'text', onClick: () => goDetail(toPerson(record)) }, () => '详情'),
  },
]

onMounted(() => {
  load()
  loadTop()
  loadIpStats()
})
</script>

<template>
  <div class="pilot-page">
    <!-- Arco 主题定制验证：通过设计令牌 CSS 变量覆盖，作用域仅限本页 -->
    <a-card class="pilot-hero">
      <div class="pilot-hero-inner">
        <div>
          <h2 style="margin: 0 0 4px">穿搭博主 · Arco Design 试点</h2>
          <span style="color: var(--color-text-3); font-size: 13px">
            本页由 Arco Design（@arco-design/web-vue）重建，与 Naive UI 版（/persons）并存对比；
            数据接口完全一致。主色/圆角已通过设计令牌定制验证。
          </span>
        </div>
        <a-space>
          <a-button size="small" @click="router.push('/persons')">← 返回 Naive UI 版</a-button>
        </a-space>
      </div>
    </a-card>

    <!-- 筛选区 -->
    <a-card class="pilot-card">
      <a-space :size="12" wrap>
        <a-input
          v-model="search"
          placeholder="搜索昵称 / 小红书号 / IP属地"
          allow-clear
          style="width: 240px"
          @press-enter="page = 1; load()"
          @clear="page = 1; load()"
        >
          <template #prefix>🔍</template>
        </a-input>
        <a-select
          v-model="platform"
          :options="platformOptions"
          style="width: 140px"
          @change="page = 1; load()"
        />
        <a-select
          v-model="sort"
          :options="sortOptions"
          style="width: 140px"
          @change="page = 1; load()"
        />
        <a-button type="primary" @click="page = 1; load()">查询</a-button>
      </a-space>
    </a-card>

    <!-- 博主 IP 属地概览（Arco 统计卡片，验证数据展示组件） -->
    <a-card v-if="ipStats" class="pilot-card" title="博主 IP 属地 Top 5">
      <a-space :size="24" wrap>
        <a-statistic
          v-for="(item, i) in ipStats.items.slice(0, 5)"
          :key="item.ip_location"
          :title="`${i + 1}. ${item.ip_location}`"
          :value="item.count"
          :precision="0"
        />
      </a-space>
    </a-card>

    <!-- 人物表格 -->
    <a-card class="pilot-card">
      <a-table
        :columns="columns"
        :data="items"
        :loading="loading"
        :pagination="false"
        :bordered="{ wrapper: true, cell: true }"
        row-key="id"
        :scroll="{ x: 1100 }"
        style="margin-bottom: 16px"
      >
        <template #empty>
          <a-empty description="还没有穿搭博主" />
        </template>
      </a-table>

      <a-pagination
        :total="total"
        :current="page"
        :page-size="pageSize"
        show-total
        show-page-size
        :page-size-options="[10, 20, 50]"
        style="justify-content: flex-end"
        @change="onPageChange"
        @page-size-change="(size: number) => { pageSize = size; page = 1; load() }"
      />
    </a-card>

    <!-- 热门排行（Arco 列表卡片） -->
    <a-card v-if="topPersons.length > 0" class="pilot-card" title="热门人物（按素材数）">
      <a-list :bordered="false" :split="false">
        <a-list-item v-for="(p, i) in topPersons" :key="p.id" class="pilot-top-row" @click="goDetail(p)">
          <template #actions>
            <span style="color: var(--color-text-3); font-size: 12px">{{ p.inspiration_count ?? 0 }} 素材</span>
          </template>
          <div class="pilot-top-inner">
            <span class="pilot-top-rank">{{ i + 1 }}</span>
            <a-avatar :size="28">
              <img
                v-if="p.face_thumb_path || p.avatar_path"
                :src="getFileUrl(p.face_thumb_path || (p.avatar_path as string))"
                :alt="p.name"
              />
              <span v-else aria-hidden="true">👤</span>
            </a-avatar>
            <span class="pilot-top-name">{{ p.name }}</span>
          </div>
        </a-list-item>
      </a-list>
    </a-card>
  </div>
</template>

<style scoped>
/* 主题已由全局 styles/arco-theme.css 提供（主色/圆角/字体设计令牌）；
   本页仅保留布局与局部样式 */
.pilot-page {
  max-width: 1100px;
  margin: 0 auto;
}

.pilot-hero {
  margin-bottom: 16px;
  border-radius: 10px;
}

.pilot-hero-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.pilot-card {
  margin-bottom: 12px;
  border-radius: 10px;
}

/* 人物单元格：头像 + 名称垂直居中 */
.pilot-person-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pilot-person-name {
  font-weight: 500;
  font-size: 14px;
  line-height: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.pilot-avatar-icon {
  width: 60%;
  height: 60%;
  color: var(--color-text-4);
  fill: currentColor;
}

/* 热门排行 */
.pilot-top-row {
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 8px;
  transition: background 0.15s;
}

.pilot-top-row:hover {
  background: var(--color-fill-2);
}

.pilot-top-inner {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pilot-top-rank {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--color-fill-3);
  color: var(--color-text-2);
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.pilot-top-name {
  font-weight: 500;
}
</style>
