<script setup lang="ts">
/** 人物列表区（穿搭博主/职业模特共用）：搜索筛选、表格、导入（博主专属）、新建/编辑/删除。
 *
 * 编排层：核心逻辑在 usePersonList / useBloggerImport，补全与跳过管理在
 * PersonEnrichManager（内部 useBloggerEnrich），本组件只做组装与事件接线。
 *
 * 人物组（方案 B，仅博主）：同组折叠为一条主记录（多平台徽标），
 * 展开行显示组内其余账号；展开/绑定操作在 usePersonList 中实现。 */

import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { Person } from '@shared/types/person'
import { usePersonList } from '@/composables/usePersonList'
import { useBloggerImport } from '@/composables/useBloggerImport'
import { useAvatarFill } from '@/composables/useAvatarFill'
import { usePersonBatch } from '@/composables/usePersonBatch'
import PersonDuplicateModal from '@/components/person/PersonDuplicateModal.vue'
import PersonFormModal from '@/components/person/PersonFormModal.vue'
import PersonImportAlert from '@/components/person/PersonImportAlert.vue'
import PersonTopList from '@/components/person/PersonTopList.vue'
import PersonEnrichManager from '@/components/person/PersonEnrichManager.vue'
import IpStatsPanel from '@/components/person/IpStatsPanel.vue'
import StatCardGrid from '@/components/common/StatCardGrid.vue'
import { PERSON_PLATFORM_LABELS } from '@shared/types/person'
import { getFileUrl } from '@/api/inspirations'

const props = defineProps<{ kind: 'blogger' | 'model' }>()

const {
  store,
  kindLabel,
  bloggerStats,
  platformCount,
  platformOptions,
  sortOptions,
  sourceOptions,
  showForm,
  editingPerson,
  openCreate,
  goDetail,
  onSearchKeydown,
  onSorterChange,
  onSortPicked,
  columns,
  topPersons,
  missingTotal,
  loadMissingCount,
  expandedGroupIds,
  setExpandedGroupIds,
} = usePersonList(props.kind)

const { importResult, importError, handleImportCsv, dismissImportResult } = useBloggerImport(
  props.kind,
)

/** 博主补全弹窗开关（PersonEnrichManager v-model） */
const enrichOpen = ref(false)

/** 头像批量补全：提交后台任务 → 轮询进度 → 完成后刷新列表 */
const {
  running: avatarRunning,
  progressText: avatarProgress,
  fillAvatars,
  stop: stopAvatarFill,
} = useAvatarFill(async () => {
  await store.reload()
  await loadCoverage()
})

/** 内置确认弹窗点「确定」：默认只补空缺（不覆盖手动头像） */
async function onFillAvatars() {
  await fillAvatars()
}

/** 批量操作（多选 / 纳入追踪 / 删除 / 清头像 / 同名建议 / 头像覆盖率） */
const {
  selectedKeys,
  selectedCount,
  busy: batchBusy,
  coverage,
  duplicateGroups,
  duplicateOpen,
  duplicateLoading,
  onSelectionChange,
  clearSelection,
  loadCoverage,
  batchPromote,
  batchDelete,
  clearSelectedAvatars,
  openDuplicates,
  mergeGroup,
  removeEmptyAccounts,
} = usePersonBatch(props.kind, async () => {
  await store.reload()
  await loadMissingCount()
})

onMounted(() => {
  void loadCoverage()
})

onBeforeUnmount(stopAvatarFill)

/** 补全弹窗关闭后：刷新缺失计数与博主列表 */
function afterEnrich() {
  void loadMissingCount()
  void store.reload()
}

/**
 * 表格展开/折叠回调（仅博主表格配置了 expandable，模特不会触发）。
 * 注意必须用 @expanded-change 事件语法：Arco 内部 emit("expandedChange")，
 * 编译器会把事件名 camelize 为 onExpandedChange 才能匹配；写成
 * :on-expanded-change 的 v-bind 形式时 prop 名保持 kebab-case，事件会丢失。
 */
function handleExpandedChange(keys: Array<string | number>) {
  if (props.kind !== 'blogger') return
  setExpandedGroupIds(keys.map(Number))
}
</script>

<template>
  <div>
    <!-- 博主数量统计（总数 / 平台分布 / 头像覆盖；复用公共统计卡片组件） -->
    <StatCardGrid
      v-if="kind === 'blogger' && bloggerStats"
      :span="6"
      :items="[
        { title: '博主总数', value: bloggerStats.total, highlight: true },
        { title: '小红书博主', value: platformCount('xiaohongshu') },
        { title: '抖音博主', value: platformCount('douyin') },
        {
          title: '已设头像',
          text: coverage ? `${coverage.with_avatar}/${coverage.total}` : '-',
          note: coverage ? `还能补 ${coverage.fillable} 位` : undefined,
        },
      ]"
    />

    <!-- 导入结果提示（成功统计 + 失败明细，仅博主有导入入口） -->
    <PersonImportAlert
      v-if="kind === 'blogger'"
      :result="importResult"
      :error="importError"
      @dismiss="dismissImportResult"
    />

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
          v-if="kind === 'blogger'"
          v-model="store.source"
          :options="sourceOptions"
          style="width: 130px"
          @change="store.reload()"
        />
        <a-select
          v-model="store.sort"
          :options="sortOptions"
          style="width: 140px"
          @change="onSortPicked"
        />
      </a-space>
    </a-card>

    <!-- 博主 IP 属地统计（ArcoChart 封装，横向柱状图展示地域分布） -->
    <IpStatsPanel v-if="kind === 'blogger'" />

    <!-- 人物表格 -->
    <a-card size="small" class="table-card">
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 10px">
        <a-button v-if="kind === 'blogger'" type="secondary" @click="enrichOpen = true">
          一键补全主页<template v-if="missingTotal > 0">（{{ missingTotal }}）</template>
        </a-button>
        <!-- 批量补头像：从博主自己的素材里裁真实人像，默认只补空缺 -->
        <a-popconfirm
          v-if="kind === 'blogger'"
          :content="
            '从 TA 自己的素材里裁真实人像作头像（优先用已匹配到 TA 的人脸，否则扫最近的素材），' +
            '只补还没有头像的博主（不覆盖手动设置过的）。约 1 秒/位，需人脸服务在跑，' +
            '可在任务中心看进度。'
          "
          :ok-loading="avatarRunning"
          @ok="onFillAvatars"
        >
          <a-button type="secondary" :loading="avatarRunning">
            批量补全头像<template v-if="avatarRunning">（{{ avatarProgress }}）</template>
          </a-button>
        </a-popconfirm>
        <a-upload
          v-if="kind === 'blogger'"
          accept=".csv,text/csv"
          :show-file-list="false"
          :custom-request="handleImportCsv"
          :limit="1"
        >
          <a-button type="secondary">点击上传 CSV</a-button>
        </a-upload>
        <a-button v-if="kind === 'blogger'" type="secondary" @click="openDuplicates">
          同名建议
        </a-button>
        <a-button type="primary" @click="openCreate">新建{{ kindLabel }}</a-button>
      </div>

      <!-- 多选批量操作栏：只在有选择时出现，避免常驻占版面 -->
      <div v-if="selectedCount > 0" class="batch-bar">
        <span class="batch-count">已选 {{ selectedCount }} 位</span>
        <a-space :size="8">
          <a-popconfirm
            v-if="kind === 'blogger'"
            content="把选中的「自动登记」博主纳入追踪？之后「一键获取素材」会增量下载 TA 的主页作品。非自动登记的不受影响。"
            @ok="batchPromote"
          >
            <a-button size="small" type="outline" status="warning" :disabled="batchBusy">
              批量纳入追踪
            </a-button>
          </a-popconfirm>
          <a-popconfirm
            v-if="kind === 'blogger'"
            content="清除选中博主的头像？会删掉头像文件并回到名字首字占位（手动挑过的也会清）。"
            @ok="clearSelectedAvatars"
          >
            <a-button size="small" type="outline" :disabled="batchBusy">清除所选头像</a-button>
          </a-popconfirm>
          <a-popconfirm
            content="删除选中的博主？有素材关联的会被后端拦下并给出原因（不会误删素材）。"
            @ok="batchDelete"
          >
            <a-button size="small" status="danger" :disabled="batchBusy">批量删除</a-button>
          </a-popconfirm>
          <a-button size="small" type="text" @click="clearSelection">取消选择</a-button>
        </a-space>
      </div>

      <a-table
        :columns="columns"
        :data="store.persons"
        :loading="store.loading"
        row-key="id"
        :bordered="false"
        :scroll="{ x: 1180 }"
        :pagination="false"
        :row-selection="{ type: 'checkbox', showCheckedAll: true }"
        :selected-keys="selectedKeys"
        @selection-change="onSelectionChange"
        :expandable="props.kind === 'blogger' ? { expandedRowKeys: expandedGroupIds } : undefined"
        @expanded-change="handleExpandedChange"
        @sorter-change="onSorterChange"
      >
        <template #expand-row="{ record }">
          <!-- 人物组展开行：显示组内其余账号（方案 B） -->
          <div v-if="(record as Person).group_members?.length" class="group-expand">
            <div class="group-expand-title">
              同一个人物（{{
                PERSON_PLATFORM_LABELS[(record as Person).group_platforms?.[0] ?? 'other']
              }}
              {{
                (record as Person).group_platforms?.length
                  ? `+${(record as Person).group_platforms!.length - 1}`
                  : ''
              }}
              …共 {{ (record as Person).group_members!.length + 1 }} 个账号）
            </div>
            <div
              v-for="m in (record as Person).group_members!"
              :key="m.id"
              class="group-member"
              @click="goDetail(m)"
            >
              <img
                v-if="m.avatar_path"
                :src="getFileUrl(m.avatar_path)"
                :alt="m.name"
                class="group-member-avatar"
              />
              <span v-else class="group-member-avatar-fallback">{{ m.name.slice(0, 1) }}</span>
              <span class="group-member-name">{{ m.name }}</span>
              <a-tag size="small">{{ PERSON_PLATFORM_LABELS[m.platform] ?? m.platform }}</a-tag>
              <span class="group-member-count">{{ m.inspiration_count ?? 0 }} 素材</span>
            </div>
            <div class="group-expand-hint">点击账号查看该平台素材</div>
          </div>
          <div v-else class="group-expand-empty">无同组账号</div>
        </template>
      </a-table>

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
    <PersonTopList :persons="topPersons" @go-detail="goDetail" />

    <!-- 新建/编辑对话框：新建后回第一页（新数据按最新排序在最前）；
         编辑后保持当前页刷新，不再跳回第一页 -->
    <PersonFormModal
      v-model:show="showForm"
      :kind="kind"
      :person="editingPerson"
      @saved="(p: Person) => (editingPerson ? store.load(true) : store.reload())"
    />

    <!-- 同名建议弹窗（仅穿搭博主） -->
    <PersonDuplicateModal
      v-if="kind === 'blogger'"
      v-model:visible="duplicateOpen"
      :groups="duplicateGroups"
      :loading="duplicateLoading"
      :busy="batchBusy"
      @merge="mergeGroup"
      @remove-empty="removeEmptyAccounts"
    />

    <!-- 博主主页信息补全 + 已跳过管理弹窗（仅穿搭博主） -->
    <PersonEnrichManager
      v-if="kind === 'blogger'"
      v-model:visible="enrichOpen"
      @finished="afterEnrich"
    />
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 12px;
}

.table-card {
  margin-bottom: 12px;
}

/* 多选批量操作栏：有选中项时才出现（贴在表格上方，跟着选择走） */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  padding: 8px 12px;
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  background: var(--color-fill-1);
}

.batch-count {
  font-weight: 600;
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

/* 人物组展开行（方案 B） */
.group-expand {
  padding: 4px 8px 8px 32px;
}
.group-expand-title {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 8px;
}
.group-member {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  margin: 0 8px 8px 0;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  background: #fafafa;
}
.group-member:hover {
  border-color: #2a78d6;
  background: #eef4fd;
}
.group-member-name {
  font-weight: 500;
  font-size: 13px;
}
.group-member-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
}
.group-member-avatar-fallback {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #eef4fd;
  color: #2a78d6;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}
.group-member-count {
  font-size: 12px;
  color: #9ca3af;
}
.group-expand-empty {
  padding: 8px 8px 8px 32px;
  font-size: 12px;
  color: #9ca3af;
}
.group-expand-hint {
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 4px;
}
</style>
