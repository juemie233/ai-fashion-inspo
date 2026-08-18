<script setup lang="ts">
/** 人物列表区（穿搭博主/职业模特共用）：搜索筛选、表格、导入（博主专属）、新建/编辑/删除。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  useMessage,
  NButton,
  NPopconfirm,
  type DataTableColumns,
  type UploadCustomRequestOptions,
} from 'naive-ui'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getFileUrl } from '@/api/inspirations'
import { bloggersApi, importBloggersCsv, modelsApi, type PersonIpStats } from '@/api/persons'
import { usePersonsStore, type PersonKind } from '@/stores/persons'
import type { Person, PersonImportResult } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

echarts.use([BarChart, TooltipComponent, GridComponent, CanvasRenderer])

const props = defineProps<{ kind: PersonKind }>()

const router = useRouter()
const message = useMessage()
const store = usePersonsStore(props.kind)

const kindLabel = computed(() => (props.kind === 'blogger' ? '穿搭博主' : '职业模特'))

/** 来源中文映射 */
const SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  ai_generated: 'AI 生成',
}

// ── 筛选选项 ──

const platformOptions = [
  { label: '全部平台', value: '' },
  ...Object.entries(PERSON_PLATFORM_LABELS).map(([value, label]) => ({ label, value })),
]
const sortOptions = [
  { label: '最新创建', value: 'newest' },
  { label: '名称排序', value: 'name' },
  { label: '素材数最多', value: 'count' },
]

// ── 新建 / 编辑 / 删除 ──

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
    const api = props.kind === 'blogger' ? bloggersApi : modelsApi
    await api.remove(person.id)
    message.success(`已删除${kindLabel.value}「${person.name}」`)
    // 保持当前页：本页仅剩一条且不在第一页时回退一页，否则按当前页刷新
    if (store.persons.length === 1 && store.page > 1) {
      await store.setPage(store.page - 1)
    } else {
      await store.load(true)
    }
  } catch (e) {
    message.error(getApiErrorMessage(e, '删除失败'))
  }
}

/** 跳转人物详情 */
function goDetail(person: Person) {
  router.push({ path: `/persons/${person.id}`, query: { kind: props.kind } })
}

/** 搜索输入：回车触发（兼容中文输入法，compositionend 期间不误触） */
function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.isComposing) {
    store.reload()
  }
}

// ── CSV 导入（博主专属）──

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
    const result = await importBloggersCsv(file as File)
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

// ── 表格列定义 ──

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
    width: 230,
    // 固定在表格右侧：列宽合计超过容器宽度出现横向滚动时，操作按钮始终可见
    fixed: 'right',
    // 注：render 中必须使用导入的组件对象（NButton/NPopconfirm），
    // 字符串组件名在 render 场景可能解析失败导致按钮不渲染
    render: (row) =>
      h('div', { class: 'row-actions' }, [
        h(
          NButton,
          { size: 'small', quaternary: true, onClick: () => goDetail(row) },
          { default: () => '详情' }
        ),
        h(
          NButton,
          { size: 'small', secondary: true, onClick: () => openEdit(row) },
          { default: () => '编辑' }
        ),
        h(
          NPopconfirm,
          {
            onPositiveClick: () => handleDelete(row),
          },
          {
            trigger: () =>
              h(NButton, { size: 'small', type: 'error' }, { default: () => '删除' }),
            default: () => `确定删除${kindLabel.value}「${row.name}」？仅当该人物无关联素材时才可删除。`,
          }
        ),
      ]),
  },
]

// ── 热门排行（侧栏展示）──

const topPersons = ref<Person[]>([])

async function loadTop() {
  try {
    const api = props.kind === 'blogger' ? bloggersApi : modelsApi
    topPersons.value = (await api.fetchTop(5)) ?? []
  } catch {
    // 排行加载失败不阻塞主列表
  }
}

// ── 博主 IP 属地统计（ECharts 横向柱状图，仅博主展示）──

const ipStats = ref<PersonIpStats | null>(null)
const ipChartRef = ref<HTMLDivElement | null>(null)
let ipChart: echarts.ECharts | null = null

/** 加载博主 IP 属地统计并渲染柱状图 */
async function loadIpStats() {
  if (props.kind !== 'blogger') return
  try {
    ipStats.value = await bloggersApi.fetchIpStats(30)
    await nextTick()
    renderIpChart()
  } catch {
    // 统计加载失败不阻塞列表
  }
}

/** 渲染横向柱状图：y 轴地区（最多在上），x 轴人数 */
function renderIpChart() {
  if (!ipChartRef.value || !ipStats.value || ipStats.value.items.length === 0) return
  if (!ipChart || ipChart.isDisposed()) ipChart = echarts.init(ipChartRef.value)
  const items = [...ipStats.value.items].reverse()
  ipChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: items.map((i) => i.ip_location) },
    series: [
      {
        type: 'bar',
        data: items.map((i) => i.count),
        barMaxWidth: 18,
        itemStyle: { color: '#18a058', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right' },
      },
    ],
  })
}

function handleIpChartResize() {
  ipChart?.resize()
}

onMounted(async () => {
  await store.load(true)
  await loadTop()
  if (props.kind === 'blogger') {
    await loadIpStats()
    window.addEventListener('resize', handleIpChartResize)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleIpChartResize)
  ipChart?.dispose()
  ipChart = null
})
</script>

<template>
  <div>
    <!-- 导入结果提示（成功统计 + 失败明细，仅博主有导入入口） -->
    <template v-if="kind === 'blogger'">
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
    </template>

    <!-- 筛选区：搜索/平台/排序 -->
    <n-card size="small" class="filter-card">
      <n-space :size="12" wrap>
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
    </n-card>

    <!-- 博主 IP 属地统计（横向柱状图，展示地域分布） -->
    <n-card
      v-if="kind === 'blogger'"
      size="small"
      class="ipstats-card"
      title="博主 IP 属地统计"
    >
      <template #header-extra>
        <n-text depth="3" style="font-size: 12px">共 {{ ipStats?.total ?? 0 }} 位博主</n-text>
      </template>
      <div ref="ipChartRef" class="ipstats-chart" />
      <n-empty
        v-if="ipStats && ipStats.items.length === 0"
        description="暂无 IP 属地数据（可从 CSV 导入或编辑博主补充）"
        size="small"
        style="padding: 24px 0"
      />
    </n-card>

    <!-- 人物表格 -->
    <n-card size="small" class="table-card">
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 10px">
        <n-upload
          v-if="kind === 'blogger'"
          accept=".csv,text/csv"
          :show-file-list="false"
          :custom-request="handleImportCsv"
          :max="1"
        >
          <n-button secondary>导入 CSV</n-button>
        </n-upload>
        <n-button type="primary" @click="openCreate">新建{{ kindLabel }}</n-button>
      </div>

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
        :description="`还没有${kindLabel}，点击右上角「新建${kindLabel}」开始录入`"
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
          <span style="color: #999; font-size: 12px">{{ p.inspiration_count ?? 0 }} 素材</span>
        </div>
      </n-space>
    </n-card>

    <!-- 新建/编辑对话框 -->
    <PersonFormModal
      v-model:show="showForm"
      :kind="kind"
      :person="editingPerson"
      @saved="store.reload()"
    />
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}

/* IP 属地统计卡片与图表 */
.ipstats-card {
  margin-bottom: 12px;
}

.ipstats-chart {
  height: 320px;
  width: 100%;
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
