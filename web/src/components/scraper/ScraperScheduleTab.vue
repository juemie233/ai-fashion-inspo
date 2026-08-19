<script setup lang="ts">
/** 定时采集页签：计划创建表单 + 计划列表（编辑/启用/停用、立即执行、删除）。 */

import { h, onMounted, ref } from 'vue'
import { NButton, NPopconfirm, NSwitch, NTag } from 'naive-ui'
import { useScraperSchedules, INTERVAL_OPTIONS, intervalLabel, SORT_MODE_LABELS } from '@/composables/useScraperSchedules'
import { PLATFORM_LABELS } from '@/composables/useScraperTasks'
import type { ScraperSchedule } from '@/types/scraper'

const {
  schedules, creating, updatingId, togglingId, runningId, deletingId,
  formPlatform, formKeywords, formMaxCount, formSortMode, formInterval, formEnabled,
  keywordOptions,
  loadSchedules, createSchedule, updateSchedule, toggleSchedule, runNow, deleteSchedule, formatDate,
} = useScraperSchedules()

onMounted(loadSchedules)

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
    { title: '平台', key: 'platform', width: 90, render: (r: ScraperSchedule) => PLATFORM_LABELS[r.platform] || r.platform },
    { title: '关键词', key: 'keywords', ellipsis: { tooltip: true }, render: (r: ScraperSchedule) => h('span', [
      h('span', r.keywords.join(', ') || '-'),
      r.keywords.length > 1 ? h('span', { style: { marginLeft: '6px' } }, [h(NTag, { size: 'tiny', type: 'info', bordered: false }, { default: () => '轮换' })]) : null,
    ]) },
    { title: '数量', key: 'max_count', width: 60 },
    { title: '间隔', key: 'interval', width: 100, render: (r: ScraperSchedule) => intervalLabel(r.interval_minutes) },
    { title: '排序', key: 'sort_mode', width: 70, render: (r: ScraperSchedule) => r.platform === 'xiaohongshu' ? SORT_MODE_LABELS[r.sort_mode || 'general'] : '-' },
    { title: '下次执行', key: 'next_run_at', width: 150, render: (r: ScraperSchedule) => formatDate(r.next_run_at) },
    { title: '上次执行', key: 'last_run_at', width: 150, render: (r: ScraperSchedule) => formatDate(r.last_run_at) },
    { title: '已执行', key: 'run_count', width: 65 },
    { title: '启用', key: 'enabled', width: 70, render: (r: ScraperSchedule) => h(NSwitch, { value: r.enabled, size: 'small', loading: togglingId.value === r.id, onUpdateValue: () => toggleSchedule(r) }) },
    { title: '操作', key: 'actions', width: 210, render: (r: ScraperSchedule) => h('span', { style: { display: 'flex', gap: '4px' } }, [
      h(NButton, { size: 'tiny', type: 'primary', ghost: true, loading: runningId.value === r.id, onClick: () => runNow(r) }, '立即执行'),
      h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, '编辑'),
      h(NPopconfirm, { onPositiveClick: () => deleteSchedule(r) }, { trigger: () => h(NButton, { size: 'tiny', type: 'error', ghost: true, loading: deletingId.value === r.id }, '删除'), default: () => '确定删除此定时计划？' }),
    ]) },
  ]
}
</script>

<template>
<div>
  <n-card title="新建定时计划" size="small" style="margin-bottom:16px">
    <n-form label-placement="left" label-width="80" size="small">
      <n-form-item label="平台">
        <n-select v-model:value="formPlatform" :options="[{label:'小红书',value:'xiaohongshu'},{label:'抖音',value:'douyin'}]" style="width:180px" />
      </n-form-item>
      <n-form-item label="关键词">
        <a-select
          v-model="formKeywords"
          multiple
          allow-create
          :options="keywordOptions"
          placeholder="选择轮换关键词，或输入新关键词后回车（回车即添加）"
          style="width:100%"
        />
        <template #feedback>
          <span style="font-size:12px;color:#999">
            每次执行时轮流使用其中一个关键词（可多选，可选历史关键词或手动输入后回车创建）
          </span>
        </template>
      </n-form-item>
      <n-form-item label="数量">
        <n-input-number v-model:value="formMaxCount" :min="1" :max="500" style="width:100px" />
      </n-form-item>
      <n-form-item v-if="formPlatform==='xiaohongshu'" label="排序">
        <n-select v-model:value="formSortMode" :options="SORT_OPTIONS" style="width:120px" />
      </n-form-item>
      <n-form-item label="间隔">
        <n-select v-model:value="formInterval" :options="INTERVAL_OPTIONS" style="width:140px" />
      </n-form-item>
      <n-form-item label="启用">
        <n-switch v-model:value="formEnabled" />
      </n-form-item>
      <n-button type="primary" :loading="creating" @click="createSchedule">创建计划</n-button>
    </n-form>
  </n-card>

  <n-card title="计划列表" size="small">
    <n-data-table v-if="schedules.length" :columns="getColumns()" :data="schedules" :bordered="false" :row-key="(r: ScraperSchedule) => r.id" size="small" />
    <n-empty v-else description="暂无定时计划 — 创建后由后端按间隔自动采集" size="medium" />
    <n-alert type="info" style="margin-top:12px">
      ⏰ 定时采集由后端调度循环执行（每 30 秒检查一次到期计划）。
      小红书定时任务依赖调试模式 Chrome 保持运行，建议先在「采集任务」页签启动 Chrome。
    </n-alert>
  </n-card>

  <!-- 编辑计划弹窗 -->
  <n-modal v-model:show="showEdit" preset="card" title="编辑定时计划" style="width:480px">
    <n-form label-placement="left" label-width="80" size="small">
      <n-form-item label="平台">
        <span>{{ PLATFORM_LABELS[editPlatform] || editPlatform }}</span>
      </n-form-item>
      <n-form-item label="关键词">
        <a-select
          v-model="editKeywords"
          multiple
          allow-create
          :options="keywordOptions"
          placeholder="选择轮换关键词，或输入新关键词后回车（回车即添加）"
          style="width:100%"
        />
      </n-form-item>
      <n-form-item label="数量">
        <n-input-number v-model:value="editMaxCount" :min="1" :max="500" style="width:100px" />
      </n-form-item>
      <n-form-item v-if="editPlatform==='xiaohongshu'" label="排序">
        <n-select v-model:value="editSortMode" :options="SORT_OPTIONS" style="width:120px" />
      </n-form-item>
      <n-form-item label="间隔">
        <n-select v-model:value="editInterval" :options="INTERVAL_OPTIONS" style="width:140px" />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-button size="small" @click="showEdit = false">取消</n-button>
      <n-button size="small" type="primary" :loading="updatingId !== null" @click="submitEdit">保存</n-button>
    </template>
  </n-modal>
</div>
</template>
