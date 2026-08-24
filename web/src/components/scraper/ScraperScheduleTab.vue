<script setup lang="ts">
/** 定时采集页签：计划创建表单 + 计划列表（编辑/启用/停用、立即执行、删除）。 */

import { h, onMounted, ref } from 'vue'
import { Button, Popconfirm, Switch, Tag } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { renderTimeCell } from '@/utils/format'
import {
  useScraperSchedules,
  INTERVAL_OPTIONS,
  intervalLabel,
  SORT_MODE_LABELS,
} from '@/composables/useScraperSchedules'
import { PLATFORM_LABELS } from '@/composables/useScraperTasks'
import type { ScraperSchedule } from '@/types/scraper'

const {
  schedules,
  creating,
  updatingId,
  togglingId,
  runningId,
  deletingId,
  formPlatform,
  formKeywords,
  formMaxCount,
  formSortMode,
  formInterval,
  formEnabled,
  keywordOptions,
  loadSchedules,
  createSchedule,
  updateSchedule,
  toggleSchedule,
  runNow,
  deleteSchedule,
  formatDate,
} = useScraperSchedules()

onMounted(() => {
  loadSchedules()
  loadHashtags()
})

// ===== 话题库（采集话题标签存档 → 定时采集关键词闭环）=====
/** 话题存档列表：{name, seen_count, blogger_name}，按热度加载前 50 */
const hashtags = ref<Array<{ name: string; seen_count: number; blogger_name: string | null }>>([])
const hashtagLoading = ref(false)

async function loadHashtags() {
  hashtagLoading.value = true
  try {
    const { data } = await apiClient.get<{
      items: Array<{ name: string; seen_count: number; blogger_name: string | null }>
    }>('/scraper/hashtags', { params: { sort: 'count', min_count: 1, limit: 50 } })
    hashtags.value = data.items
  } catch {
    // 话题库加载失败静默（不影响计划创建）
    hashtags.value = []
  } finally {
    hashtagLoading.value = false
  }
}

/** 点击话题加入当前关键词（去重；不覆盖已有关键词） */
function addHashtagToKeywords(name: string, target: string[]) {
  if (!target.includes(name)) target.push(name)
}

// ===== 编辑弹窗状态 =====
const showEdit = ref(false)
const editingId = ref<number | null>(null)
const editPlatform = ref('xiaohongshu')
const editKeywords = ref<string[]>([])
const editMaxCount = ref(20)
const editSortMode = ref('general')
const editInterval = ref(1440)

const SORT_OPTIONS = [
  { label: '综合', value: 'general' },
  { label: '最新', value: 'latest' },
  { label: '最热', value: 'popular' },
]

/** 打开编辑弹窗，用当前计划填充表单 */
function openEdit(r: ScraperSchedule) {
  editingId.value = r.id
  editPlatform.value = r.platform
  editKeywords.value = [...r.keywords]
  editMaxCount.value = r.max_count
  editSortMode.value = r.sort_mode || 'general'
  editInterval.value = r.interval_minutes
  showEdit.value = true
}

/** 提交编辑：成功后关闭弹窗 */
async function submitEdit() {
  if (editingId.value === null) return
  const ok = await updateSchedule(editingId.value, {
    keywords: editKeywords.value,
    max_count: editMaxCount.value,
    sort_mode: editPlatform.value === 'xiaohongshu' ? editSortMode.value : null,
    interval_minutes: editInterval.value,
  })
  if (ok) showEdit.value = false
}

/** 计划列表列定义（render 用函数式写法，避免闭包内 ref 不更新问题） */
function getColumns() {
  return [
    {
      title: '平台',
      dataIndex: 'platform',
      width: 90,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return PLATFORM_LABELS[r.platform] || r.platform
      },
    },
    {
      title: '关键词',
      dataIndex: 'keywords',
      ellipsis: true,
      tooltip: true,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return h('span', [
          h('span', r.keywords.join(', ') || '-'),
          r.keywords.length > 1
            ? h('span', { style: { marginLeft: '6px' } }, [
                h(Tag, { size: 'small', color: 'arcoblue' }, { default: () => '轮换' }),
              ])
            : null,
        ])
      },
    },
    { title: '数量', dataIndex: 'max_count', width: 60 },
    {
      title: '间隔',
      dataIndex: 'interval',
      width: 100,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return intervalLabel(r.interval_minutes)
      },
    },
    {
      title: '排序',
      dataIndex: 'sort_mode',
      width: 70,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return r.platform === 'xiaohongshu' ? SORT_MODE_LABELS[r.sort_mode || 'general'] : '-'
      },
    },
    {
      title: '下次执行',
      dataIndex: 'next_run_at',
      width: 150,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return renderTimeCell(formatDate(r.next_run_at))
      },
    },
    {
      title: '上次执行',
      dataIndex: 'last_run_at',
      width: 150,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return renderTimeCell(formatDate(r.last_run_at))
      },
    },
    { title: '已执行', dataIndex: 'run_count', width: 65 },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 70,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return h(Switch, {
          modelValue: r.enabled,
          size: 'small',
          loading: togglingId.value === r.id,
          onChange: () => toggleSchedule(r),
        })
      },
    },
    {
      title: '操作',
      dataIndex: 'actions',
      width: 210,
      render: ({ record }: { record: unknown }) => {
        const r = record as ScraperSchedule
        return h('span', { style: { display: 'flex', gap: '4px' } }, [
          h(
            Button,
            {
              size: 'mini',
              type: 'primary',
              loading: runningId.value === r.id,
              onClick: () => runNow(r),
            },
            () => '立即执行',
          ),
          h(Button, { size: 'mini', onClick: () => openEdit(r) }, () => '编辑'),
          h(
            Popconfirm,
            { content: '确定删除此定时计划？', onOk: () => deleteSchedule(r) },
            {
              default: () =>
                h(
                  Button,
                  { size: 'mini', status: 'danger', loading: deletingId.value === r.id },
                  () => '删除',
                ),
            },
          ),
        ])
      },
    },
  ]
}
</script>

<template>
  <div>
    <a-card title="新建定时计划" size="small" style="margin-bottom: 16px">
      <a-form
        :model="{
          formPlatform,
          formKeywords,
          formMaxCount,
          formSortMode,
          formInterval,
          formEnabled,
        }"
        label-align="left"
        :label-col-style="{ width: '80px' }"
        size="small"
      >
        <a-form-item label="平台">
          <a-select
            v-model="formPlatform"
            :options="[
              { label: '小红书', value: 'xiaohongshu' },
              { label: '抖音', value: 'douyin' },
            ]"
            style="width: 180px"
          />
        </a-form-item>
        <a-form-item label="关键词">
          <a-select
            v-model="formKeywords"
            multiple
            allow-create
            :options="keywordOptions"
            placeholder="选择轮换关键词，或输入新关键词后回车（回车即添加）"
            style="width: 100%"
          />
          <template #extra>
            <span style="font-size: 12px; color: #999">
              每次执行时轮流使用其中一个关键词（可多选，可选历史关键词或手动输入后回车创建）
            </span>
          </template>
        </a-form-item>
        <!-- 话题库：采集话题存档 → 点击加入关键词（仅小红书，话题来自按博主采集详情页正文） -->
        <a-form-item v-if="formPlatform === 'xiaohongshu'" label="话题库">
          <a-spin :loading="hashtagLoading" style="display: block">
            <div class="hashtag-pool">
              <a-tag
                v-for="topic in hashtags"
                :key="topic.name"
                class="hashtag-tag"
                color="arcoblue"
                :title="`出现 ${topic.seen_count} 次${topic.blogger_name ? `，来源博主：${topic.blogger_name}` : ''} — 点击加入关键词`"
                @click="addHashtagToKeywords(topic.name, formKeywords)"
              >
                #{{ topic.name }}（{{ topic.seen_count }}）
              </a-tag>
              <a-empty
                v-if="!hashtagLoading && hashtags.length === 0"
                description="暂无话题存档 — 按博主采集笔记正文中的 #话题 会自动入库"
                size="mini"
                style="width: 100%"
              />
            </div>
          </a-spin>
          <template #extra>
            <a-space :size="8">
              <span style="font-size: 12px; color: #999"
                >点击话题加入关键词，复用博主常发话题建定时采集</span
              >
              <a-button size="mini" type="text" @click="loadHashtags">刷新</a-button>
            </a-space>
          </template>
        </a-form-item>
        <a-form-item label="数量">
          <a-input-number v-model="formMaxCount" :min="1" :max="500" style="width: 100px" />
        </a-form-item>
        <a-form-item v-if="formPlatform === 'xiaohongshu'" label="排序">
          <a-select v-model="formSortMode" :options="SORT_OPTIONS" style="width: 120px" />
        </a-form-item>
        <a-form-item label="间隔">
          <a-select v-model="formInterval" :options="INTERVAL_OPTIONS" style="width: 140px" />
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model="formEnabled" />
        </a-form-item>
        <a-button type="primary" :loading="creating" @click="createSchedule">创建计划</a-button>
      </a-form>
    </a-card>

    <a-card title="计划列表" size="small">
      <a-table
        v-if="schedules.length"
        :columns="getColumns()"
        :data="schedules"
        :bordered="false"
        :row-key="(r: ScraperSchedule) => String(r.id)"
        size="small"
        :pagination="false"
      />
      <a-empty v-else description="暂无定时计划 — 创建后由后端按间隔自动采集" />
      <a-alert type="info" style="margin-top: 12px">
        ⏰ 定时采集由后端调度循环执行（每 30 秒检查一次到期计划）。 小红书定时任务依赖调试模式
        Chrome 保持运行，建议先在「采集任务」页签启动 Chrome。
      </a-alert>
    </a-card>

    <!-- 编辑计划弹窗 -->
    <a-modal v-model:visible="showEdit" title="编辑定时计划" :footer="false" :width="480">
      <a-form
        :model="{ editPlatform, editKeywords, editMaxCount, editSortMode, editInterval }"
        label-align="left"
        :label-col-style="{ width: '80px' }"
        size="small"
      >
        <a-form-item label="平台">
          <span>{{ PLATFORM_LABELS[editPlatform] || editPlatform }}</span>
        </a-form-item>
        <a-form-item label="关键词">
          <a-select
            v-model="editKeywords"
            multiple
            allow-create
            :options="keywordOptions"
            placeholder="选择轮换关键词，或输入新关键词后回车（回车即添加）"
            style="width: 100%"
          />
        </a-form-item>
        <!-- 编辑弹窗同样提供话题库快捷加入 -->
        <a-form-item v-if="editPlatform === 'xiaohongshu'" label="话题库">
          <div class="hashtag-pool">
            <a-tag
              v-for="topic in hashtags"
              :key="topic.name"
              class="hashtag-tag"
              color="arcoblue"
              :title="`出现 ${topic.seen_count} 次 — 点击加入关键词`"
              @click="addHashtagToKeywords(topic.name, editKeywords)"
            >
              #{{ topic.name }}（{{ topic.seen_count }}）
            </a-tag>
            <a-empty
              v-if="hashtags.length === 0"
              description="暂无话题存档"
              size="mini"
              style="width: 100%"
            />
          </div>
        </a-form-item>
        <a-form-item label="数量">
          <a-input-number v-model="editMaxCount" :min="1" :max="500" style="width: 100px" />
        </a-form-item>
        <a-form-item v-if="editPlatform === 'xiaohongshu'" label="排序">
          <a-select v-model="editSortMode" :options="SORT_OPTIONS" style="width: 120px" />
        </a-form-item>
        <a-form-item label="间隔">
          <a-select v-model="editInterval" :options="INTERVAL_OPTIONS" style="width: 140px" />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button size="small" @click="showEdit = false">取消</a-button>
        <a-button size="small" type="primary" :loading="updatingId !== null" @click="submitEdit"
          >保存</a-button
        >
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
/* 话题库：标签池点击加入关键词 */
.hashtag-pool {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 160px;
  overflow-y: auto;
  width: 100%;
}

.hashtag-tag {
  cursor: pointer;
  user-select: none;
}

.hashtag-tag:hover {
  border-color: rgb(var(--arcoblue-6));
}
</style>
