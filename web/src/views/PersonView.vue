<script setup lang="ts">
/** 人物管理页：职业模特 / 穿搭博主的列表、筛选、新建、编辑与删除。
 *
 * UI 区分：内容类型（person_type）以「类型筛选 + 表格徽标」贯穿呈现，
 * 职业模特（model）与穿搭博主（blogger）一目了然。
 */

import { h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { DataTableColumns, UploadCustomRequestOptions } from 'naive-ui'
import { getFileUrl } from '@/api/inspirations'
import { deletePerson, fetchTopPersons, importPersonsCsv } from '@/api/persons'
import { usePersonsStore } from '@/stores/persons'
import type { Person, PersonImportResult } from '@shared/types/person'
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

// ── CSV 导入状态 ──
const importResult = ref<PersonImportResult | null>(null)
const importError = ref('')

/** 处理 CSV 导入（n-upload custom-request）：上传 → 展示结果 → 刷新列表 */
async function handleImportCsv(options: UploadCustomRequestOptions) {
  const file = options.file.file
  importResult.value = null
  importError.value = ''
  if (!file) {
    message.error('未获取到文件，请重新选择')
    return
  }
  try {
    const result = await importPersonsCsv(file as File)
    importResult.value = result
    if (result.failed > 0) {
      message.warning(
        `导入完成：新增 ${result.imported}，更新 ${result.updated}，失败 ${result.failed}`,
      )
    } else {
      message.success(`导入成功：新增 ${result.imported}，更新 ${result.updated}`)
    }
    await store.reload()
  } catch (e) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail
    importError.value = detail || '导入失败'
    message.error(importError.value)
  } finally {
    // 完成后清空文件列表，允许重复选择同一文件
    options.onFinish?.()
  }
}

/** 关闭导入结果提示 */
function dismissImportResult() {
  importResult.value = null
  importError.value = ''
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
            // 无头像时用通用人形图标占位，避免名字首字与名称并排造成「杨杨晨晨」式重复
            : h('span', { class: 'avatar-fallback', 'aria-hidden': 'true' }, '👤'),
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
    title: '小红书ID',
    key: 'xhs_id',
    width: 130,
    render: (row) => row.xhs_id || '-',
  },
  {
    title: 'IP属地',
    key: 'ip_location',
    width: 90,
    render: (row) => row.ip_location || '-',
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
    // 固定在表格右侧：列宽合计超过容器宽度出现横向滚动时，操作按钮始终可见
    fixed: 'right',
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
            default: () => `确定删除人物「${row.name}」？仅当该人物无关联素材时才可删除。`,
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
        <n-upload
          accept=".csv,text/csv"
          :show-file-list="false"
          :custom-request="handleImportCsv"
          :max="1"
        >
          <n-button secondary>导入 CSV</n-button>
        </n-upload>
        <n-button type="primary" @click="openCreate">新建人物</n-button>
      </n-space>
    </div>

    <!-- 导入结果提示（成功统计 + 失败明细） -->
    <n-alert
      v-if="importResult"
      :type="importResult.failed > 0 ? 'warning' : 'success'"
      closable
      style="margin-bottom: 12px"
      @close="dismissImportResult"
    >
      <template #header>
        导入完成：新增 {{ importResult.imported }} 人，更新 {{ importResult.updated }} 人
        <template v-if="importResult.skipped > 0">，跳过 {{ importResult.skipped }} 行（CSV 内重复）</template>
        <template v-if="importResult.failed > 0">，失败 {{ importResult.failed }} 行</template>
      </template>
      <div v-if="importResult.failed > 0" style="max-height: 180px; overflow: auto">
        <div
          v-for="err in importResult.errors"
          :key="err.row"
          style="font-size: 12px; line-height: 1.8"
        >
          第 {{ err.row }} 行{{ err.nickname ? `（${err.nickname}）` : '' }}：{{ err.reason }}
        </div>
        <n-text v-if="importResult.errors.length < importResult.failed" depth="3" style="font-size: 12px">
          … 共 {{ importResult.failed }} 行失败，仅展示前 {{ importResult.errors.length }} 条
        </n-text>
      </div>
    </n-alert>
    <n-alert
      v-if="importError"
      type="error"
      closable
      style="margin-bottom: 12px"
      @close="dismissImportResult"
    >
      {{ importError }}
    </n-alert>

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
            placeholder="搜索昵称 / 小红书号 / IP属地"
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
        :scroll-x="1160"
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
  font-size: 18px;
  line-height: 1;
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
