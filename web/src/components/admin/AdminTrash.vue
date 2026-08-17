<script setup lang="ts">
/** 垃圾桶管理：查看/筛选软删除素材，支持恢复、彻底删除与清空（含清理过期）。 */

import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  fetchTrash,
  restoreInspiration,
  deleteInspiration,
  emptyTrash,
  getFileUrl,
  TRASH_REASON_OPTIONS,
  type TrashReason,
  type InspirationOut,
} from '@/api/inspirations'
import { formatSize, shortenText } from '@/utils/format'

const message = useMessage()

// ── 列表与分页 ──
const items = ref<InspirationOut[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const reasonFilter = ref<TrashReason | ''>('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

/** 垃圾桶保留天数（后端配置下发，0 表示禁用自动回收、不自动清理） */
const retentionDays = ref(0)

/** 是否启用自动回收（保留期 > 0 时才自动清理过期素材） */
const autoCleanupEnabled = computed(() => retentionDays.value > 0)

async function load() {
  loading.value = true
  try {
    const data = await fetchTrash({
      page: page.value,
      size: pageSize,
      reason: reasonFilter.value || undefined,
    })
    items.value = data.items
    total.value = data.total
    if (data.trash_retention_days) {
      retentionDays.value = data.trash_retention_days
    }
  } catch {
    message.error('加载垃圾桶失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

/** 按删除原因筛选 */
function onReasonChange() {
  page.value = 1
  load()
}

/** 剩余保留天数（自动回收禁用时返回空，前端不展示） */
function daysRemaining(deletedAt?: string | null): string {
  if (!autoCleanupEnabled.value || !deletedAt) return ''
  const deleted = new Date(deletedAt).getTime()
  const expire = deleted + retentionDays.value * 86400_000
  const days = Math.max(0, Math.ceil((expire - Date.now()) / 86400_000))
  return `${days} 天`
}

/** 移入来源与原因展示（紧凑）：手动移入 / 自动移动（质量审核）+ 删除原因 + 精简审核结论 */
function trashSourceLabel(item: InspirationOut): string {
  const reason = item.trash_reason || '未知'
  const base = item.trash_source === 'auto' ? '自动移动' : '手动移入'
  const note = item.quality_reason ? `：${shortenText(item.quality_reason)}` : ''
  return `${base} · ${reason}${note}`
}

/** 完整原因文案（title 悬停提示用）：含未精简的审核结论全文 */
function trashSourceFull(item: InspirationOut): string {
  const reason = item.trash_reason || '未知'
  const base = item.trash_source === 'auto' ? '自动移动（质量审核）' : '手动移入'
  const note = item.quality_reason ? `：${item.quality_reason}` : ''
  return `${base} · ${reason}${note}`
}

// ── 单条操作 ──
const restoring = ref<Set<string>>(new Set())
const deleting = ref<Set<string>>(new Set())

async function restore(id: string) {
  if (restoring.value.has(id)) return
  restoring.value = new Set(restoring.value).add(id)
  try {
    await restoreInspiration(id)
    message.success('已恢复')
    await load()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '恢复失败')
  } finally {
    restoring.value = new Set(restoring.value)
    restoring.value.delete(id)
  }
}

async function permanentDelete(id: string) {
  if (deleting.value.has(id)) return
  deleting.value = new Set(deleting.value).add(id)
  try {
    await deleteInspiration(id)
    message.success('已彻底删除')
    await load()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = new Set(deleting.value)
    deleting.value.delete(id)
  }
}

// ── 批量清空 ──
const emptying = ref(false)
const cleaningExpired = ref(false)

async function emptyAll() {
  emptying.value = true
  try {
    const r = await emptyTrash(false)
    message.success(`已清空 ${r.deleted} 个素材，释放 ${formatSize(r.freed_bytes)}`)
    page.value = 1
    await load()
  } catch {
    message.error('清空失败')
  } finally {
    emptying.value = false
  }
}

async function cleanExpired() {
  cleaningExpired.value = true
  try {
    const r = await emptyTrash(true)
    message.success(`已清理 ${r.deleted} 个过期素材，释放 ${formatSize(r.freed_bytes)}`)
    await load()
  } catch {
    message.error('清理失败')
  } finally {
    cleaningExpired.value = false
  }
}
</script>

<template>
  <div class="trash-panel">
    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <n-tag type="warning" size="small" :bordered="false">
          共 {{ total }} 个垃圾桶素材{{ autoCleanupEnabled ? `（${retentionDays} 天后自动清理）` : '（不会自动清理）' }}
        </n-tag>
        <n-select
          v-model:value="reasonFilter"
          :options="[{ label: '全部原因', value: '' }, ...TRASH_REASON_OPTIONS]"
          size="small"
          style="width: 130px"
          placeholder="删除原因"
          @update:value="onReasonChange"
        />
      </div>
      <div class="toolbar-right">
        <n-popconfirm v-if="autoCleanupEnabled" @positive-click="cleanExpired">
          <template #trigger>
            <n-button size="small" :loading="cleaningExpired">清理过期</n-button>
          </template>
          仅清理超过 {{ retentionDays }} 天保留期的素材，仍在保留期内的保留。
        </n-popconfirm>
        <n-popconfirm @positive-click="emptyAll">
          <template #trigger>
            <n-button size="small" type="error" secondary :loading="emptying">清空垃圾桶</n-button>
          </template>
          彻底删除垃圾桶中全部素材并释放磁盘空间，不可恢复。
        </n-popconfirm>
      </div>
    </div>

    <!-- 列表 -->
    <n-spin :show="loading">
      <div v-if="items.length === 0 && !loading" class="empty">
        <n-empty description="垃圾桶是空的 🎉" />
      </div>
      <div v-else class="grid">
        <div v-for="item in items" :key="item.id" class="trash-card">
          <video
            v-if="item.media_type === 'video' && !item.thumbnail_path"
            :src="getFileUrl(item.file_path)"
            muted
            playsinline
            preload="metadata"
            class="thumb"
          />
          <img
            v-else
            :src="getFileUrl(item.thumbnail_path || item.file_path)"
            :alt="item.source_author || '垃圾桶素材'"
            class="thumb"
            loading="lazy"
          />
          <div class="meta">
            <n-tag size="tiny" type="error" :bordered="false" :title="trashSourceFull(item)">
              {{ trashSourceLabel(item) }}
            </n-tag>
            <span v-if="autoCleanupEnabled" class="days">剩余 {{ daysRemaining(item.deleted_at) }}</span>
          </div>
          <div class="actions">
            <n-button
              size="tiny"
              type="primary"
              secondary
              :loading="restoring.has(item.id)"
              @click="restore(item.id)"
            >
              恢复
            </n-button>
            <n-popconfirm @positive-click="permanentDelete(item.id)">
              <template #trigger>
                <n-button
                  size="tiny"
                  type="error"
                  quaternary
                  :loading="deleting.has(item.id)"
                >
                  彻底删除
                </n-button>
              </template>
              彻底删除后不可恢复，确定继续？
            </n-popconfirm>
          </div>
        </div>
      </div>
    </n-spin>

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <n-pagination
        v-model:page="page"
        :page-count="totalPages"
        :page-size="pageSize"
        @update:page="load"
      />
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
.trash-card {
  border: 1px solid var(--n-border-color);
  border-radius: 10px;
  overflow: hidden;
  background: var(--n-color);
  display: flex;
  flex-direction: column;
}
.thumb {
  width: 100%;
  aspect-ratio: 3 / 4;
  object-fit: cover;
  display: block;
  background: #f5f5f5;
}
.meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  padding: 8px 10px 0;
}
.days {
  font-size: 12px;
  color: #999;
}
.actions {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px 10px;
}
.empty {
  padding: 48px 0;
}
.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
