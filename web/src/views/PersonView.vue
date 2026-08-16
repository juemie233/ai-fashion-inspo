<script setup lang="ts">
/** 人物管理页：职业模特 / 穿搭博主的列表、筛选、新建、编辑与删除。
 *
 * UI 区分：内容类型（person_type）以「类型筛选 + 表格徽标」贯穿呈现，
 * 职业模特（model）与穿搭博主（blogger）一目了然。
 */

import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { getFileUrl } from '@/api/inspirations'
import { deletePerson, fetchTopPersons } from '@/api/persons'
import { usePersonsStore } from '@/stores/persons'
import type { Person } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS, PERSON_TYPE_LABELS } from '@shared/types/person'
import PersonTypeTag from '@/components/person/PersonTypeTag.vue'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

const router = useRouter()
const message = useMessage()
const store = usePersonsStore()

/** 来源中文映射 */
const SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  ai_generated: 'AI 生成',
}

/** 类型筛选选项：核心 UI 区分入口 */
const typeFilterOptions = [
  { label: '全部人物', value: '' },
  { label: `👗 ${PERSON_TYPE_LABELS.blogger}`, value: 'blogger' },
  { label: `📷 ${PERSON_TYPE_LABELS.model}`, value: 'model' },
]

/** 平台筛选选项 */
const platformOptions = [
  { label: '全部平台', value: '' },
  ...Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value })),
]

/** 排序选项 */
const sortOptions = [
  { label: '最新创建', value: 'newest' },
  { label: '按名称', value: 'name' },
  { label: '素材数最多', value: 'count' },
]

// ── 新建/编辑对话框状态 ──
const showForm = ref(false)
const editingPerson = ref<Person | null>(null)

function openCreate() {
  editingPerson.value = null
  showForm.value = true
}

function openEdit(person: Person) {
  editingPerson.value = person
  showForm.value = true
}

/** 删除人物 */
async function handleDelete(person: Person) {
  try {
    await deletePerson(person.id)
    message.success(`已删除人物「${person.name}」`)
    // 保持当前页：本页仅剩一条且不在第一页时回退一页，否则按当前页刷新
    if (store.persons.length === 1 && store.page > 1) {
      await store.setPage(store.page - 1)
    } else {
      await store.load(true)
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

/** 跳转人物详情 */
function goDetail(person: Person) {
  router.push(`/persons/${person.id}`)
}

/** 搜索输入：回车触发（兼容中文输入法，compositionend 期间不误触） */
function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.isComposing) {
    store.reload()
  }
}

/** 表格列定义 */
const columns: DataTableColumns<Person> = [
  {
    title: '人物',
    key: 'name',
    minWidth: 160,
    render: (row) =>
      h('div', { class: 'person-cell' }, [
        h('span', { class: 'person-avatar' }, [
          row.avatar_path
            ? h('img', { src: getFileUrl(row.avatar_path), class: 'avatar-img', alt: row.name })
            : h('span', { class: 'avatar-fallback' }, row.name.slice(0, 1)),
        ]),
        h('span', { class: 'person-name' }, row.name),
      ]),
  },
  {
    title: '内容类型',
    key: 'person_type',
    width: 110,
    render: (row) => h(PersonTypeTag, { type: row.person_type }),
  },
  {
    title: '平台',
    key: 'platform',
    width: 90,
    render: (row) => PERSON_PLATFORM_LABELS[row.platform] || row.platform,
  },
  {
    title: '素材数',
    key: 'inspiration_count',
    width: 80,
    render: (row) => h('span', String(row.inspiration_count ?? 0)),
  },
  {
    title: '来源',
    key: 'source',
    width: 90,
    render: (row) => SOURCE_LABELS[row.source || 'manual'] || row.source,
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 160,
    render: (row) =>
      row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '-',
  },
  {
    title: '操作',
    key: 'actions',
    width: 210,
    render: (row) =>
      h('div', { class: 'row-actions' }, [
        h(
          'n-button',
          { size: 'small', onClick: () => goDetail(row) },
          { default: () => '详情' }
        ),
        h(
          'n-button',
          { size: 'small', quaternary: true, onClick: () => openEdit(row) },
          { default: () => '编辑' }
        ),
        h(
          'n-popconfirm',
          {
            onPositiveClick: () => handleDelete(row),
          },
          {
            trigger: () =>
              h(
                'n-button',
                { size: 'small', type: 'error', quaternary: true },
                { default: () => '删除' }
              ),
            default: () => `确定删除人物「${row.name}」？其素材不会被删除，仅解除关联。`,
          }
        ),
      ]),
  },
]

/** 热门排行（侧栏展示） */
const topPersons = ref<Person[]>([])

onMounted(async () => {
  await store.load(true)
  try {
    topPersons.value = (await fetchTopPersons(5)) ?? []
  } catch {
    // 排行加载失败不阻塞主列表
  }
})
</script>

<template>
  <div class="person-page">
    <div class="page-header">
      <div>
        <h2>人物管理</h2>
        <n-text depth="3" style="font-size: 13px">
          管理职业模特与穿搭博主，按人物浏览素材、聚合风格画像
        </n-text>
      </div>
      <n-space>
        <n-button secondary @click="store.reload()">刷新</n-button>
        <n-button type="primary" @click="openCreate">新建人物</n-button>
      </n-space>
    </div>

    <!-- 筛选区：类型筛选为核心 UI 区分入口 -->
    <n-card size="small" class="filter-card">
      <n-space vertical :size="12">
        <n-radio-group
          v-model:value="store.personType"
          name="person-type-filter"
          @update:value="store.reload()"
        >
          <n-radio-button v-for="opt in typeFilterOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </n-radio-button>
        </n-radio-group>

        <n-space>
          <n-input
            v-model:value="store.search"
            placeholder="搜索人物名称"
            clearable
            style="width: 240px"
            @keydown="onSearchKeydown"
            @clear="store.reload()"
          >
            <template #prefix>🔍</template>
          </n-input>
          <n-select
            v-model:value="store.platform"
            :options="platformOptions"
            style="width: 140px"
            @update:value="store.reload()"
          />
          <n-select
            v-model:value="store.sort"
            :options="sortOptions"
            style="width: 140px"
            @update:value="store.reload()"
          />
        </n-space>
      </n-space>
    </n-card>

    <!-- 人物表格 -->
    <n-card size="small" class="table-card">
      <n-data-table
        :columns="columns"
        :data="store.persons"
        :loading="store.loading"
        :row-key="(row: Person) => row.id"
        :bordered="false"
      />

      <n-pagination
        v-if="store.total > store.size"
        style="margin-top: 16px; justify-content: flex-end"
        :page="store.page"
        :page-size="store.size"
        :item-count="store.total"
        @update:page="store.setPage"
      />

      <n-empty
        v-if="!store.loading && !store.error && store.persons.length === 0"
        description="还没有人物，点击右上角「新建人物」开始录入"
        style="margin-top: 48px"
      />

      <!-- 加载失败错误态：与「无数据」明确区分 -->
      <n-result
        v-if="store.error"
        status="error"
        title="加载失败"
        :description="store.error"
        style="margin-top: 32px"
      >
        <template #footer>
          <n-button @click="store.reload()">重试</n-button>
        </template>
      </n-result>
    </n-card>

    <!-- 热门排行 -->
    <n-card v-if="topPersons.length > 0" size="small" class="top-card" title="热门人物（按素材数）">
      <n-space vertical :size="8">
        <div
          v-for="(p, i) in topPersons"
          :key="p.id"
          class="top-row"
          @click="goDetail(p)"
        >
          <span class="top-rank">{{ i + 1 }}</span>
          <span class="top-name">{{ p.name }}</span>
          <PersonTypeTag :type="p.person_type" />
          <n-text depth="3" style="margin-left: auto">{{ p.inspiration_count ?? 0 }} 素材</n-text>
        </div>
      </n-space>
    </n-card>

    <!-- 新建/编辑对话框 -->
    <PersonFormModal
      v-model:show="showForm"
      :person="editingPerson"
      @saved="store.reload()"
    />
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

.filter-card {
  margin-bottom: 12px;
}

.table-card {
  margin-bottom: 12px;
}

/* 人物单元格：头像 + 名称 */
.person-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.person-avatar {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 50%;
  overflow: hidden;
  display: inline-flex;
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
  font-size: 15px;
  color: #4a5a7a;
  font-weight: 600;
}

.person-name {
  font-weight: 500;
}

.row-actions {
  display: flex;
  gap: 4px;
  align-items: center;
}

/* 热门排行 */
.top-card {
  margin-top: 12px;
}

.top-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.top-row:hover {
  background: #f5f7fa;
}

.top-rank {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #e8ecf2;
  color: #3b4a63;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.top-name {
  font-weight: 500;
}
</style>
