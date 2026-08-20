<script setup lang="ts">
/** 人物列表区（穿搭博主/职业模特共用）：搜索筛选、表格、导入（博主专属）、新建/编辑/删除。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Message,
  Button,
  Popconfirm,
  Avatar,
  type TableColumnData,
  type RequestOption,
  type UploadRequest,
} from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'
import {
  bloggersApi,
  enrichMissingProfiles,
  fetchMissingProfiles,
  importBloggersCsv,
  modelsApi,
  type MissingProfileBlogger,
} from '@/api/persons'
import { usePersonsStore, type PersonKind } from '@/stores/persons'
import type { Person, PersonImportResult } from '@shared/types/person'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import PersonFormModal from '@/components/person/PersonFormModal.vue'
import IpStatsPanel from '@/components/person/IpStatsPanel.vue'

const props = defineProps<{ kind: PersonKind }>()

const router = useRouter()
const route = useRoute()
const store = usePersonsStore(props.kind)

const kindLabel = computed(() => (props.kind === 'blogger' ? '穿搭博主' : '职业模特'))

// ── 列表状态 URL 持久化：页码/搜索/平台/排序写入 query，刷新与详情往返后原样恢复 ──

/** 把列表上下文（kind + 页码 + 搜索 + 平台 + 排序）同步到 URL（replace 不堆历史） */
function syncUrl() {
  const query: Record<string, string> = { kind: props.kind }
  if (store.page > 1) query.page = String(store.page)
  if (store.search.trim()) query.q = store.search.trim()
  if (store.platform) query.platform = store.platform
  if (store.sort !== 'count') query.sort = store.sort
  router.replace({ path: '/persons', query })
}

/** 列表上下文变化时同步 URL：翻页/搜索/平台/排序任意变更即反映到地址栏 */
watch(
  () => [store.page, store.search, store.platform, store.sort] as const,
  () => syncUrl(),
)

/** 从 URL query 恢复列表上下文（组件挂载时调用，刷新/详情返回后保持原状态）；
 *  默认排序为「素材数最多」（count），URL 未显式指定时按此进入 */
function restoreFromUrl() {
  const q = route.query
  const page = Number(q.page)
  store.page = Number.isInteger(page) && page > 1 ? page : 1
  store.search = typeof q.q === 'string' ? q.q : ''
  store.platform = typeof q.platform === 'string' ? q.platform : ''
  store.sort = q.sort === 'name' || q.sort === 'count' ? q.sort : 'count'
}

/** 加载后修正页码越界（如删除后总页数减少），并同步 URL */
async function loadAndSync(force: boolean = true) {
  await store.load(force)
  const maxPage = Math.max(1, Math.ceil(store.total / store.size))
  if (store.page > maxPage) {
    store.page = maxPage
    await store.load(true)
  }
  syncUrl()
}

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
  { label: '素材数最多', value: 'count' },
  { label: '最新创建', value: 'newest' },
  { label: '名称排序', value: 'name' },
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
    Message.success(`已删除${kindLabel.value}「${person.name}」`)
    // 保持当前页：本页仅剩一条且不在第一页时回退一页，否则按当前页刷新
    if (store.persons.length === 1 && store.page > 1) {
      await store.setPage(store.page - 1)
    } else {
      await store.load(true)
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
  }
}

/** 跳转人物详情：携带列表上下文（kind/页码/搜索/平台/排序），详情页返回或刷新后可恢复 */
function goDetail(person: Person) {
  const query: Record<string, string> = { kind: props.kind }
  if (store.page > 1) query.page = String(store.page)
  if (store.search.trim()) query.q = store.search.trim()
  if (store.platform) query.platform = store.platform
  if (store.sort !== 'count') query.sort = store.sort
  router.push({ path: `/persons/${person.id}`, query })
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

/** 处理 CSV 导入（a-upload custom-request）：上传 → 展示结果 → 刷新列表。
 * Arco 的 custom-request 期望同步返回（UploadRequest），异步逻辑用内部 IIFE 包裹。 */
function handleImportCsv(options: RequestOption): UploadRequest {
  void (async () => {
    const file = options.fileItem.file
    importResult.value = null
    importError.value = ''
    if (!file) {
      Message.error('未获取到文件，请重新选择')
      return
    }
    try {
      const result = await importBloggersCsv(file)
      importResult.value = result
      if (result.failed > 0) {
        Message.warning(
          `导入完成：新增 ${result.imported}，更新 ${result.updated}，失败 ${result.failed}`,
        )
      } else {
        Message.success(`导入成功：新增 ${result.imported}，更新 ${result.updated}`)
      }
      await store.reload()
      options.onSuccess?.()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      importError.value = detail || '导入失败'
      Message.error(importError.value)
      options.onError?.(e as Error)
    }
  })()
  return {}
}

/** 关闭导入结果提示 */
function dismissImportResult() {
  importResult.value = null
  importError.value = ''
}

// ── 表格列定义 ──

const columns: TableColumnData[] = [
  {
    title: '人物',
    dataIndex: 'name',
    minWidth: 160,
    render: ({ record }) => {
      const row = record as Person
      return h('div', { class: 'person-cell' }, [
        h(
          Avatar,
          { size: 40 },
          {
            default: () =>
              // 展示优先级：人脸小图（自动裁剪）→ 手动头像 → SVG 人形占位
              row.face_thumb_path || row.avatar_path
                ? h('img', {
                    src: getFileUrl(row.face_thumb_path || (row.avatar_path as string)),
                    alt: row.name,
                  })
                : // 无头像时用通用人形图标占位，避免名字首字与名称并排造成「杨杨晨晨」式重复
                  h('svg', { viewBox: '0 0 24 24', class: 'avatar-icon', 'aria-hidden': 'true' }, [
                    h('path', {
                      d: 'M12 12c2.7 0 4.9-2.2 4.9-4.9S14.7 2.2 12 2.2 7.1 4.4 7.1 7.1 9.3 12 12 12zm0 2.4c-3.4 0-10.1 1.7-10.1 5v2.4h20.2v-2.4c0-3.3-6.7-5-10.1-5z',
                    }),
                  ]),
          },
        ),
        h('span', { class: 'person-name' }, row.name),
      ])
    },
  },
  {
    title: '平台',
    dataIndex: 'platform',
    width: 90,
    render: ({ record }) => {
      const row = record as Person
      return PERSON_PLATFORM_LABELS[row.platform] || row.platform
    },
  },
  {
    title: '小红书ID',
    dataIndex: 'xhs_id',
    width: 130,
    render: ({ record }) => (record as Person).xhs_id || '-',
  },
  {
    title: 'IP属地',
    dataIndex: 'ip_location',
    width: 90,
    render: ({ record }) => (record as Person).ip_location || '-',
  },
  {
    title: '素材数',
    dataIndex: 'inspiration_count',
    width: 80,
    render: ({ record }) => h('span', String((record as Person).inspiration_count ?? 0)),
  },
  // 「来源」列仅职业模特展示（穿搭博主不显示该列，其余能力不受影响）
  ...(props.kind === 'model'
    ? ([
        {
          title: '来源',
          dataIndex: 'source',
          width: 90,
          render: ({ record }) => {
            const row = record as Person
            return SOURCE_LABELS[row.source || 'manual'] || row.source
          },
        },
      ] as TableColumnData[])
    : []),
  {
    title: '创建时间',
    dataIndex: 'created_at',
    width: 160,
    render: ({ record }) => {
      const row = record as Person
      return row.created_at ? new Date(row.created_at).toLocaleString('zh-CN') : '-'
    },
  },
  {
    title: '操作',
    dataIndex: 'actions',
    width: 230,
    // 固定在表格右侧：列宽合计超过容器宽度出现横向滚动时，操作按钮始终可见
    fixed: 'right',
    // 注：render 中必须使用导入的组件对象（Button/Popconfirm），
    // 字符串组件名在 render 场景可能解析失败导致按钮不渲染
    render: ({ record }) => {
      const row = record as Person
      return h('div', { class: 'row-actions' }, [
        h(
          Button,
          { size: 'small', type: 'text', onClick: () => goDetail(row) },
          { default: () => '详情' },
        ),
        h(
          Button,
          { size: 'small', type: 'secondary', onClick: () => openEdit(row) },
          { default: () => '编辑' },
        ),
        h(
          Popconfirm,
          {
            content: `确定删除${kindLabel.value}「${row.name}」？仅当该人物无关联素材时才可删除。`,
            onOk: () => handleDelete(row),
          },
          {
            default: () =>
              h(Button, { size: 'small', status: 'danger' }, { default: () => '删除' }),
          },
        ),
      ])
    },
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

onMounted(async () => {
  // 从 URL 恢复列表上下文（刷新 / 从详情返回时保持原页码与筛选），再加载数据
  restoreFromUrl()
  await loadAndSync(true)
  await loadTop()
  await loadMissingCount()
})

onUnmounted(() => {
  if (enrichPollTimer !== null) {
    window.clearInterval(enrichPollTimer)
    enrichPollTimer = null
  }
})

// ── 博主主页信息补全（仅穿搭博主：缺失 profile_url / platform_user_id 的博主）──

/** 缺失主页信息的博主数量（工具栏按钮徽标） */
const missingTotal = ref(0)
/** 补全弹窗开关 */
const enrichOpen = ref(false)
/** 缺失博主列表（弹窗内勾选范围，默认全选） */
const missingItems = ref<MissingProfileBlogger[]>([])
/** 勾选的博主 ID */
const selectedMissingIds = ref<Set<number>>(new Set())
const enrichBusy = ref(false)
/** 补全任务状态（轮询展示） */
const enrichTask = ref<{
  id: number
  status: string
  progress: number
  done: number
  total: number
  result: Record<string, unknown> | null
} | null>(null)
let enrichPollTimer: number | null = null

async function loadMissingCount() {
  if (props.kind !== 'blogger') return
  try {
    const data = await fetchMissingProfiles()
    missingTotal.value = data.total
  } catch {
    // 数量加载失败静默（按钮无徽标，不影响其它功能）
  }
}

/** 打开补全弹窗：拉取缺失博主列表并默认全选 */
async function openEnrichModal() {
  enrichTask.value = null
  try {
    const data = await fetchMissingProfiles()
    missingItems.value = data.items
    selectedMissingIds.value = new Set(data.items.map((b) => b.id))
    enrichOpen.value = true
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载缺失博主失败'))
  }
}

function toggleMissing(id: number, checked: boolean) {
  const next = new Set(selectedMissingIds.value)
  if (checked) {
    next.add(id)
  } else {
    next.delete(id)
  }
  selectedMissingIds.value = next
}

/** 轮询补全任务直到终态（2s 间隔） */
async function pollEnrichTask(taskId: number) {
  while (true) {
    const { data } = await apiClient.get(`/tasks/${taskId}`)
    enrichTask.value = data
    if (!['pending', 'running'].includes(data.status)) return
    await new Promise((r) => setTimeout(r, 2000))
  }
}

/** 开始补全勾选的博主 */
async function startEnrich() {
  const ids = [...selectedMissingIds.value]
  if (ids.length === 0) {
    Message.warning('请至少勾选一位博主')
    return
  }
  enrichBusy.value = true
  enrichTask.value = null
  try {
    const data = await enrichMissingProfiles(ids)
    Message.success(data.message)
    await pollEnrichTask(data.task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建补全任务失败'))
  } finally {
    enrichBusy.value = false
  }
}

/** 失败博主单独重试（用任务结果中的失败 blogger_id 发起新任务） */
async function retryFailed() {
  const results =
    (enrichTask.value?.result as { results?: Array<{ blogger_id: number; status: string }> } | null)
      ?.results ?? []
  const failedIds = results.filter((r) => r.status === 'failed').map((r) => r.blogger_id)
  if (failedIds.length === 0) {
    Message.info('没有失败的博主')
    return
  }
  enrichBusy.value = true
  enrichTask.value = null
  try {
    const data = await enrichMissingProfiles(failedIds)
    Message.success(data.message)
    await pollEnrichTask(data.task_id)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '创建重试任务失败'))
  } finally {
    enrichBusy.value = false
  }
}

/** 关闭弹窗：刷新缺失计数与博主列表 */
function closeEnrich() {
  enrichOpen.value = false
  enrichTask.value = null
  void loadMissingCount()
  void store.reload()
}

/** 补全任务结果明细（供模板展示失败原因） */
const enrichResults = computed(
  () =>
    (
      enrichTask.value?.result as {
        results?: Array<{ blogger_id: number; name: string; status: string; reason?: string }>
      } | null
    )?.results ?? [],
)
const enrichUpdated = computed(
  () => (enrichTask.value?.result as { updated?: number } | null)?.updated ?? 0,
)
const enrichFailed = computed(
  () => (enrichTask.value?.result as { failed?: number } | null)?.failed ?? 0,
)
const enrichFailedItems = computed(() => enrichResults.value.filter((r) => r.status === 'failed'))
</script>

<template>
  <div>
    <!-- 导入结果提示（成功统计 + 失败明细，仅博主有导入入口） -->
    <template v-if="kind === 'blogger'">
      <a-alert
        v-if="importResult"
        :type="importResult.failed > 0 ? 'warning' : 'success'"
        closable
        style="margin-bottom: 12px"
        @close="dismissImportResult"
      >
        <template #title>
          导入完成：新增 {{ importResult.imported }} 人，更新 {{ importResult.updated }} 人
          <template v-if="importResult.skipped > 0"
            >，跳过 {{ importResult.skipped }} 行（CSV 内重复）</template
          >
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
          <a-typography-text
            v-if="importResult.errors.length < importResult.failed"
            type="secondary"
            style="font-size: 12px"
          >
            … 共 {{ importResult.failed }} 行失败，仅展示前 {{ importResult.errors.length }} 条
          </a-typography-text>
        </div>
      </a-alert>
      <a-alert
        v-if="importError"
        type="error"
        closable
        style="margin-bottom: 12px"
        @close="dismissImportResult"
      >
        {{ importError }}
      </a-alert>
    </template>

    <!-- 筛选区：搜索/平台/排序 -->
    <a-card size="small" class="filter-card">
      <a-space :size="12" wrap>
        <a-input
          v-model="store.search"
          placeholder="搜索昵称 / 小红书号 / IP属地"
          allow-clear
          style="width: 240px"
          @keydown="onSearchKeydown"
          @clear="store.reload()"
        >
          <template #prefix>🔍</template>
        </a-input>
        <a-select
          v-model="store.platform"
          :options="platformOptions"
          style="width: 140px"
          @change="store.reload()"
        />
        <a-select
          v-model="store.sort"
          :options="sortOptions"
          style="width: 140px"
          @change="store.reload()"
        />
      </a-space>
    </a-card>

    <!-- 博主 IP 属地统计（ArcoChart 封装，横向柱状图展示地域分布） -->
    <IpStatsPanel v-if="kind === 'blogger'" />

    <!-- 人物表格 -->
    <a-card size="small" class="table-card">
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 10px">
        <a-button v-if="kind === 'blogger'" type="secondary" @click="openEnrichModal">
          一键补全主页<template v-if="missingTotal > 0">（{{ missingTotal }}）</template>
        </a-button>
        <a-upload
          v-if="kind === 'blogger'"
          accept=".csv,text/csv"
          :show-file-list="false"
          :custom-request="handleImportCsv"
          :limit="1"
        >
          <a-button type="secondary">点击上传 CSV</a-button>
        </a-upload>
        <a-button type="primary" @click="openCreate">新建{{ kindLabel }}</a-button>
      </div>

      <a-table
        :columns="columns"
        :data="store.persons"
        :loading="store.loading"
        :row-key="(row: Person) => String(row.id)"
        :bordered="false"
        :scroll="{ x: 1160 }"
        :pagination="false"
      />

      <a-pagination
        v-if="store.total > store.size"
        style="margin-top: 16px; justify-content: flex-end"
        :current="store.page"
        :page-size="store.size"
        :total="store.total"
        @change="(p: number) => store.setPage(p)"
      />

      <a-empty
        v-if="!store.loading && !store.error && store.persons.length === 0"
        :description="`还没有${kindLabel}，点击右上角「新建${kindLabel}」开始录入`"
        style="margin-top: 48px"
      />

      <!-- 加载失败错误态：与「无数据」明确区分 -->
      <a-result
        v-if="store.error"
        status="error"
        title="加载失败"
        :description="store.error"
        style="margin-top: 32px"
      >
        <template #extra>
          <a-button @click="store.reload()">重试</a-button>
        </template>
      </a-result>
    </a-card>

    <!-- 热门排行 -->
    <a-card v-if="topPersons.length > 0" size="small" class="top-card" title="热门人物（按素材数）">
      <a-space direction="vertical" :size="8">
        <div v-for="(p, i) in topPersons" :key="p.id" class="top-row" @click="goDetail(p)">
          <span class="top-rank">{{ i + 1 }}</span>
          <a-avatar :size="28">
            <img
              v-if="p.face_thumb_path || p.avatar_path"
              :src="getFileUrl(p.face_thumb_path || (p.avatar_path as string))"
              :alt="p.name"
            />
            <span v-else aria-hidden="true">👤</span>
          </a-avatar>
          <span class="top-name">{{ p.name }}</span>
          <span style="color: #999; font-size: 12px">{{ p.inspiration_count ?? 0 }} 素材</span>
        </div>
      </a-space>
    </a-card>

    <!-- 新建/编辑对话框：新建后回第一页（新数据按最新排序在最前）；
         编辑后保持当前页刷新，不再跳回第一页 -->
    <PersonFormModal
      v-model:show="showForm"
      :kind="kind"
      :person="editingPerson"
      @saved="(p: Person) => (editingPerson ? store.load(true) : store.reload())"
    />

    <!-- 博主主页信息补全弹窗（仅穿搭博主） -->
    <a-modal
      v-if="kind === 'blogger'"
      v-model:visible="enrichOpen"
      title="补全博主主页信息"
      :width="560"
      :mask-closable="false"
      :footer="false"
      @cancel="closeEnrich"
    >
      <!-- 选择补全范围 -->
      <template v-if="!enrichTask">
        <p class="enrich-tip">
          为缺失主页信息（主页链接 / 平台用户
          ID）的小红书博主自动补全：优先本地互推，缺失时按小红书号搜索匹配。 单次最多补全 20
          位（防触发风控），完成后失败博主可单独重试。
        </p>
        <div class="enrich-list">
          <div v-for="b in missingItems" :key="b.id" class="enrich-row">
            <a-checkbox
              :model-value="selectedMissingIds.has(b.id)"
              @change="(v: unknown) => toggleMissing(b.id, Boolean(v))"
            >
              <span class="enrich-name">{{ b.name }}</span>
              <span class="enrich-xhs">{{ b.xhs_id || '无小红书号' }}</span>
            </a-checkbox>
          </div>
          <a-empty v-if="missingItems.length === 0" description="暂无可补全的博主" size="small" />
        </div>
        <div class="enrich-actions">
          <a-button @click="closeEnrich">取消</a-button>
          <a-button
            type="primary"
            :loading="enrichBusy"
            :disabled="selectedMissingIds.size === 0"
            @click="startEnrich"
          >
            开始补全（{{ selectedMissingIds.size }}）
          </a-button>
        </div>
      </template>

      <!-- 任务进度与结果 -->
      <template v-else>
        <div class="enrich-progress">
          <a-tag
            size="small"
            :color="
              enrichTask.status === 'success'
                ? 'green'
                : enrichTask.status === 'failed'
                  ? 'red'
                  : 'arcoblue'
            "
          >
            {{
              {
                pending: '排队中',
                running: '补全中',
                success: '已完成',
                failed: '失败',
                cancelled: '已取消',
              }[enrichTask.status] || enrichTask.status
            }}
          </a-tag>
          <a-progress
            v-if="['pending', 'running'].includes(enrichTask.status)"
            :percent="enrichTask.progress / 100"
          />
          <a-typography-text type="secondary" style="font-size: 12px">
            处理 {{ enrichTask.done }} / {{ enrichTask.total }}
          </a-typography-text>
        </div>

        <template v-if="!['pending', 'running'].includes(enrichTask.status)">
          <a-alert
            :type="enrichFailed > 0 ? 'warning' : 'success'"
            style="margin: 12px 0"
            :message="`补全完成：成功 ${enrichUpdated} 位${enrichFailed > 0 ? `，失败 ${enrichFailed} 位` : ''}`"
          />
          <div v-if="enrichFailedItems.length > 0" class="enrich-failed">
            <div v-for="item in enrichFailedItems" :key="item.blogger_id" class="enrich-failed-row">
              <span class="enrich-name">{{ item.name }}</span>
              <span class="enrich-reason">{{ item.reason || '未知原因' }}</span>
            </div>
          </div>
          <div class="enrich-actions">
            <a-button
              v-if="enrichFailedItems.length > 0"
              type="secondary"
              :loading="enrichBusy"
              @click="retryFailed"
            >
              重试失败（{{ enrichFailedItems.length }}）
            </a-button>
            <a-button type="primary" @click="closeEnrich">完成</a-button>
          </div>
        </template>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}

.table-card {
  margin-bottom: 12px;
}

/* 人物单元格：a-avatar + 名称（无头像时 SVG 人形占位） */
.person-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar-icon {
  width: 60%;
  height: 60%;
  color: var(--color-text-4);
  fill: currentColor;
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

/* 热门排行头像：a-avatar（圆形小图，人脸缩略图或手动头像） */

.top-name {
  font-weight: 500;
}

/* 博主主页补全弹窗 */
.enrich-tip {
  font-size: 12px;
  color: #888;
  margin: 0 0 12px;
}

.enrich-list {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 14px;
}

.enrich-row {
  padding: 6px 0;
  border-bottom: 1px dashed #f2f3f5;
}

.enrich-row:last-child {
  border-bottom: none;
}

.enrich-name {
  font-size: 13px;
  margin-right: 8px;
}

.enrich-xhs {
  font-size: 12px;
  color: #999;
}

.enrich-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}

.enrich-failed {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #fde2e2;
  background: #fff7f7;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 14px;
}

.enrich-failed-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}

.enrich-reason {
  color: #c0392b;
  font-size: 12px;
  text-align: right;
}

.enrich-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 4px;
}
</style>
