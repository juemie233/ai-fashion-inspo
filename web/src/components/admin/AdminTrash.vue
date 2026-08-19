<script setup lang="ts">
/** 垃圾桶管理：查看/筛选软删除素材，支持恢复、彻底删除与清空（含清理过期）。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
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
    Message.error('加载垃圾桶失败')
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

/** 分页跳转 */
function onPageChange(p: number) {
  page.value = p
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
    Message.success('已恢复')
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '恢复失败'))
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
    Message.success('已彻底删除')
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '删除失败'))
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
    Message.success(`已清空 ${r.deleted} 个素材，释放 ${formatSize(r.freed_bytes)}`)
    page.value = 1
    await load()
  } catch {
    Message.error('清空失败')
  } finally {
    emptying.value = false
  }
}

async function cleanExpired() {
  cleaningExpired.value = true
  try {
    const r = await emptyTrash(true)
    Message.success(`已清理 ${r.deleted} 个过期素材，释放 ${formatSize(r.freed_bytes)}`)
    await load()
  } catch {
    Message.error('清理失败')
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
        <a-tag status="warning" size="small">
          共 {{ total }} 个垃圾桶素材{{ autoCleanupEnabled ? `（${retentionDays} 天后自动清理）` : '（不会自动清理）' }}
        </a-tag>
        <a-select
          v-model="reasonFilter"
          :options="[{ label: '全部原因', value: '' }, ...TRASH_REASON_OPTIONS]"
          size="small"
          style="width: 130px"
          placeholder="删除原因"
          @change="onReasonChange"
        />
      </div>
      <div class="toolbar-right">
        <a-popconfirm
          v-if="autoCleanupEnabled"
          :content="`仅清理超过 ${retentionDays} 天保留期的素材，仍在保留期内的保留。`"
          @ok="cleanExpired"
        >
          <a-button size="small" :loading="cleaningExpired">清理过期</a-button>
        </a-popconfirm>
        <a-popconfirm content="彻底删除垃圾桶中全部素材并释放磁盘空间，不可恢复。" @ok="emptyAll">
          <a-button size="small" type="secondary" status="danger" :loading="emptying">清空垃圾桶</a-button>
        </a-popconfirm>
      </div>
    </div>

    <!-- 列表 -->
    <a-spin :loading="loading" style="display: block">
      <div v-if="items.length === 0 && !loading" class="empty">
        <a-empty description="垃圾桶是空的 🎉" />
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
            <a-tag size="small" status="danger" :title="trashSourceFull(item)">
              {{ trashSourceLabel(item) }}
            </a-tag>
            <span v-if="autoCleanupEnabled" class="days">剩余 {{ daysRemaining(item.deleted_at) }}</span>
          </div>
          <div class="actions">
            <a-button
              size="mini"
              type="primary"
              :loading="restoring.has(item.id)"
              @click="restore(item.id)"
            >
              恢复
            </a-button>
            <a-popconfirm content="彻底删除后不可恢复，确定继续？" @ok="permanentDelete(item.id)">
              <a-button
                size="mini"
                type="text"
                status="danger"
                :loading="deleting.has(item.id)"
              >
                彻底删除
              </a-button>
            </a-popconfirm>
          </div>
        </div>
      </div>
    </a-spin>

    <!-- 分页 -->
    <div v-if="totalPages > 1" class="pagination-wrapper">
      <a-pagination
        :total="total"
        :current="page"
        :page-size="pageSize"
        @change="onPageChange"
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
  border: 1px solid var(--color-border-2);
  border-radius: 10px;
  overflow: hidden;
  background: var(--color-bg-2);
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
