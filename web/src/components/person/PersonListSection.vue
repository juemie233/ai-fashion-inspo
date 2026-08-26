<script setup lang="ts">
/** 人物列表区（穿搭博主/职业模特共用）：搜索筛选、表格、导入（博主专属）、新建/编辑/删除。
 *
 * 编排层：核心逻辑在 usePersonList / useBloggerImport，补全与跳过管理在
 * PersonEnrichManager（内部 useBloggerEnrich），本组件只做组装与事件接线。
 *
 * 人物组（方案 B，仅博主）：同组折叠为一条主记录（多平台徽标），
 * 展开行显示组内其余账号；展开/绑定操作在 usePersonList 中实现。 */

import { ref } from 'vue'
import type { Person } from '@shared/types/person'
import { usePersonList } from '@/composables/usePersonList'
import { useBloggerImport } from '@/composables/useBloggerImport'
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
  showForm,
  editingPerson,
  openCreate,
  goDetail,
  onSearchKeydown,
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

/** 补全弹窗关闭后：刷新缺失计数与博主列表 */
function afterEnrich() {
  void loadMissingCount()
  void store.reload()
}
</script>

<template>
  <div>
    <!-- 博主数量统计（总数 / 平台分布；复用公共统计卡片组件） -->
    <StatCardGrid
      v-if="kind === 'blogger' && bloggerStats"
      :span="8"
      :items="[
        { title: '博主总数', value: bloggerStats.total, highlight: true },
        { title: '小红书博主', value: platformCount('xiaohongshu') },
        { title: '抖音博主', value: platformCount('douyin') },
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
        <a-button v-if="kind === 'blogger'" type="secondary" @click="enrichOpen = true">
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
        :expandable="props.kind === 'blogger' ? { expandedRowKeys: expandedGroupIds } : undefined"
        :on-expanded-change="
          props.kind === 'blogger'
            ? (keys: Array<string | number>) => setExpandedGroupIds(keys.map(Number))
            : undefined
        "
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
                v-if="m.face_thumb_path || m.avatar_path"
                :src="getFileUrl(m.face_thumb_path || (m.avatar_path as string))"
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
