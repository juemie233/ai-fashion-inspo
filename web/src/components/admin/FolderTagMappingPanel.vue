<script setup lang="ts">
/** 抖音收藏夹 → 穿搭大标签映射（2026-11-24）。
 *
 * 口径：
 * - 映射按收藏夹名存，首次「获取我的收藏」前就能配置；
 * - 未配置的夹首次同步会按同名自动建标签并进入「待确认」，在这里可改名/改指/跳过；
 * - 同名标签已属其它类别时不静默复用：弹三选一（改类别 / 另起名 / 跳过）；
 * - 重命名全局标签会影响所有引用它的素材，必须看到影响面并勾选确认。
 */

import { computed, onMounted, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import {
  applyAllFolderTags,
  applyFolderTagMapping,
  deleteFolderTagMapping,
  fetchFolderTagImpact,
  fetchFolderTagMappings,
  upsertFolderTagMapping,
  type FolderTagMapping,
  type FolderTagUpsertPayload,
} from '@/api/folderTags'
import { fetchTagsGrouped, updateTag } from '@/api/tags'
import { getApiErrorMessage } from '@/utils/apiError'

const rows = ref<FolderTagMapping[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const applyingAll = ref(false)
const applyingFolder = ref<string | null>(null)

// 「待确认」只算首次同步自动建标签的项；「未配置」是还没建映射（仅扫描到/本地已有）
const pendingCount = computed(() => rows.value.filter((row) => row.needs_confirm).length)
const unconfiguredCount = computed(
  () => rows.value.filter((row) => row.mode === 'pending' && !row.needs_confirm).length,
)
const mappedCount = computed(() => rows.value.filter((row) => row.mode === 'tag').length)

/** 穿搭大标签候选（选择已有标签时用） */
const outfitOptions = ref<Array<{ label: string; value: number }>>([])
async function loadOutfitTags(): Promise<void> {
  try {
    const groups = await fetchTagsGrouped()
    const outfit = groups.find((group) => group.category === 'outfit')
    outfitOptions.value = (outfit?.tags ?? []).map((tag) => ({
      label: `${tag.name}（${tag.usage_count}）`,
      value: tag.id,
    }))
  } catch {
    outfitOptions.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    rows.value = await fetchFolderTagMappings()
  } catch (e) {
    error.value = getApiErrorMessage(e, '加载映射失败')
  } finally {
    loading.value = false
  }
}

function statusText(row: FolderTagMapping): string {
  if (row.mode === 'skip') return '已跳过'
  if (row.needs_confirm) return '待确认'
  if (row.mode === 'pending') return '未配置'
  return '已映射'
}

function statusType(row: FolderTagMapping): 'success' | 'warning' | 'default' {
  if (row.mode === 'skip') return 'default'
  if (row.needs_confirm) return 'warning'
  if (row.mode === 'pending') return 'default'
  return 'success'
}

// ── 设置映射弹窗 ──

type EditChoice = 'auto' | 'existing' | 'new' | 'skip'
const editOpen = ref(false)
const editFolder = ref('')
const editChoice = ref<EditChoice>('auto')
const editExistingTagId = ref<number | undefined>(undefined)
const editNewName = ref('')

function openEdit(row: FolderTagMapping): void {
  editFolder.value = row.folder_name
  editExistingTagId.value = row.tag_id ?? undefined
  editNewName.value = row.tag_name ?? row.folder_name
  editChoice.value = row.mode === 'skip' ? 'skip' : row.tag_id ? 'existing' : 'auto'
  editOpen.value = true
  if (outfitOptions.value.length === 0) void loadOutfitTags()
}

function buildEditPayload(): FolderTagUpsertPayload | null {
  if (editChoice.value === 'skip') {
    return { folder_name: editFolder.value, mode: 'skip' }
  }
  if (editChoice.value === 'existing') {
    if (!editExistingTagId.value) {
      Message.warning('请选择一个已有标签')
      return null
    }
    return { folder_name: editFolder.value, mode: 'tag', tag_id: editExistingTagId.value }
  }
  if (editChoice.value === 'new' && !editNewName.value.trim()) {
    Message.warning('请输入标签名称')
    return null
  }
  return {
    folder_name: editFolder.value,
    mode: 'tag',
    new_tag_name: editChoice.value === 'auto' ? undefined : editNewName.value.trim(),
  }
}

// ── 名称冲突三选一 ──

const conflictOpen = ref(false)
const conflictMessage = ref('')
const conflictPayload = ref<FolderTagUpsertPayload | null>(null)

async function saveWith(payload: FolderTagUpsertPayload): Promise<void> {
  try {
    await upsertFolderTagMapping(payload)
    Message.success(`已更新「${payload.folder_name}」的映射`)
    editOpen.value = false
    conflictOpen.value = false
    await load()
  } catch (e) {
    const detail = getApiErrorMessage(e, '保存失败')
    const status = (e as { response?: { status?: number } })?.response?.status
    if (status === 409) {
      conflictMessage.value = detail
      conflictPayload.value = payload
      conflictOpen.value = true
      return
    }
    Message.error(detail)
  }
}

async function submitEdit(): Promise<void> {
  const payload = buildEditPayload()
  if (!payload) return
  await saveWith(payload)
}

function reuseConflictName(): void {
  const payload = conflictPayload.value
  if (!payload) return
  void saveWith({ ...payload, reuse_existing_category: true })
}

function renameInstead(): void {
  // 冲突时改用编辑框里的名称：回到编辑弹窗并切到「新建标签」
  conflictOpen.value = false
  editChoice.value = 'new'
  editOpen.value = true
}

function skipConflictFolder(): void {
  const payload = conflictPayload.value
  if (!payload) return
  void saveWith({ folder_name: payload.folder_name, mode: 'skip' })
}

// ── 跳过 / 重打 ──

async function skipRow(row: FolderTagMapping): Promise<void> {
  await saveWith({ folder_name: row.folder_name, mode: 'skip' })
}

/** 删除映射（回到未配置；已打的标签关联保留） */
function clearRowMapping(row: FolderTagMapping): void {
  Modal.confirm({
    title: `清除「${row.folder_name}」的映射？`,
    content: '已打上的标签关联会保留；该收藏夹回到「未配置」，可重新设置。',
    okText: '清除',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
        await deleteFolderTagMapping(row.folder_name)
        Message.success('映射已清除')
        await load()
      } catch (e) {
        Message.error(getApiErrorMessage(e, '清除映射失败'))
      }
    },
  })
}

async function applyRow(row: FolderTagMapping): Promise<void> {
  applyingFolder.value = row.folder_name
  try {
    const result = await applyFolderTagMapping(row.folder_name)
    Message.success(`已重打 ${result.applied} 个素材`)
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重打失败'))
  } finally {
    applyingFolder.value = null
  }
}

function applyAll(): void {
  Modal.confirm({
    title: '立即按当前映射重打全部收藏夹？',
    content: '会给所有已映射收藏夹的当前成员补打大标签（幂等，只追加不覆盖）。',
    okText: '重打',
    onOk: async () => {
      applyingAll.value = true
      try {
        const result = await applyAllFolderTags()
        Message.success(`已重打 ${result.applied} 个素材`)
        await load()
      } catch (e) {
        Message.error(getApiErrorMessage(e, '重打失败'))
      } finally {
        applyingAll.value = false
      }
    },
  })
}

// ── 重命名全局标签（显式确认影响面）──

const renameOpen = ref(false)
const renameRow = ref<FolderTagMapping | null>(null)
const renameName = ref('')
const renameImpact = ref<{ usage_count: number; mapped_folders: string[] } | null>(null)
const renameConfirmed = ref(false)

async function openRename(row: FolderTagMapping): Promise<void> {
  if (!row.tag_id) return
  renameRow.value = row
  renameName.value = row.tag_name ?? ''
  renameConfirmed.value = false
  renameImpact.value = null
  renameOpen.value = true
  try {
    const impact = await fetchFolderTagImpact(row.tag_id)
    renameImpact.value = {
      usage_count: impact.usage_count,
      mapped_folders: impact.mapped_folders,
    }
  } catch {
    renameImpact.value = null
  }
}

async function submitRename(): Promise<void> {
  const row = renameRow.value
  if (!row?.tag_id) return
  const name = renameName.value.trim()
  if (!name) {
    Message.warning('请输入新的标签名称')
    return
  }
  if (!renameConfirmed.value) {
    Message.warning('请先勾选确认：该改名会影响所有引用此标签的素材')
    return
  }
  try {
    await updateTag(row.tag_id, { name })
    Message.success('标签已重命名')
    renameOpen.value = false
    await load()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重命名失败'))
  }
}

onMounted(() => {
  void load()
  void loadOutfitTags()
})
</script>

<template>
  <div class="folder-tag-panel">
    <div class="panel-head">
      <div>
        <p class="panel-hint">
          把每个抖音收藏夹映射成一个「穿搭大标签」，同步时自动给该夹素材（含视频）打上。
          映射按收藏夹名保存，首次「获取我的收藏」之前就可以在这里配置。
        </p>
        <p v-if="pendingCount > 0" class="panel-pending">
          有 {{ pendingCount }} 个收藏夹待确认：首次同步已按同名自动建标签，可在此改名、改指或跳过。
        </p>
        <p v-if="unconfiguredCount > 0" class="panel-hint">
          另有 {{ unconfiguredCount }} 个收藏夹尚未配置（还没有对应标签），点「设置标签」即可。
        </p>
      </div>
      <div class="panel-actions">
        <a-button size="small" :loading="loading" @click="() => void load()">刷新</a-button>
        <a-button
          size="small"
          type="primary"
          :loading="applyingAll"
          :disabled="mappedCount === 0"
          @click="applyAll"
        >
          立即重打全部
        </a-button>
      </div>
    </div>

    <a-alert v-if="error" type="error" :content="error" style="margin-bottom: 12px" />

    <div v-if="loading && rows.length === 0" class="panel-empty">加载中…</div>
    <div v-else-if="rows.length === 0" class="panel-empty">
      暂无收藏夹。先跑一次「先扫描收藏夹」或在下方手动添加一个名称。
    </div>

    <div v-else class="mapping-list">
      <div v-for="row in rows" :key="row.folder_name" class="mapping-row">
        <div class="mapping-name">
          <span class="name-text">{{ row.folder_name }}</span>
          <span v-if="row.local_item_count !== null" class="mapping-count">
            {{ row.local_item_count }} 个素材
          </span>
        </div>
        <div class="mapping-tag">
          <template v-if="row.mode === 'skip'">—</template>
          <template v-else-if="row.tag_name">
            {{ row.tag_name }}
            <a-tag v-if="row.tag_category !== 'outfit'" color="orange" size="small">
              非穿搭标签
            </a-tag>
          </template>
          <template v-else>未设置</template>
        </div>
        <a-tag
          :color="
            statusType(row) === 'warning'
              ? 'orange'
              : statusType(row) === 'success'
                ? 'green'
                : 'gray'
          "
          size="small"
        >
          {{ statusText(row) }}
        </a-tag>
        <div class="mapping-actions">
          <a-button size="mini" @click="openEdit(row)">设置标签</a-button>
          <a-button v-if="row.tag_id" size="mini" type="text" @click="() => void openRename(row)">
            改名
          </a-button>
          <a-button
            v-if="row.mode === 'tag'"
            size="mini"
            type="text"
            :loading="applyingFolder === row.folder_name"
            @click="() => void applyRow(row)"
          >
            重打
          </a-button>
          <a-button
            v-if="row.mode !== 'skip'"
            size="mini"
            type="text"
            @click="() => void skipRow(row)"
          >
            跳过
          </a-button>
          <a-button
            v-if="row.mode !== 'pending'"
            size="mini"
            type="text"
            status="danger"
            @click="clearRowMapping(row)"
          >
            清除映射
          </a-button>
        </div>
      </div>
    </div>

    <!-- 设置映射 -->
    <a-modal
      v-model:visible="editOpen"
      :title="`设置「${editFolder}」的大标签`"
      :width="'min(520px, calc(100vw - 32px))'"
      @ok="() => void submitEdit()"
    >
      <a-radio-group v-model="editChoice" direction="vertical">
        <a-radio value="auto">按收藏夹同名新建大标签（{{ editFolder }}）</a-radio>
        <a-radio value="new">
          新建一个大标签
          <a-input
            v-if="editChoice === 'new'"
            v-model="editNewName"
            placeholder="标签名称"
            style="width: 220px; margin-left: 8px"
          />
        </a-radio>
        <a-radio value="existing">
          选择已有大标签
          <a-select
            v-if="editChoice === 'existing'"
            v-model="editExistingTagId"
            :options="outfitOptions"
            placeholder="选择穿搭大标签"
            style="width: 240px; margin-left: 8px"
            allow-search
          />
        </a-radio>
        <a-radio value="skip">暂不映射（不打标）</a-radio>
      </a-radio-group>
    </a-modal>

    <!-- 名称冲突三选一 -->
    <a-modal
      v-model:visible="conflictOpen"
      title="标签名称冲突"
      :footer="false"
      :width="'min(560px, calc(100vw - 32px))'"
    >
      <p class="conflict-text">{{ conflictMessage }}</p>
      <div class="conflict-actions">
        <a-button type="primary" @click="reuseConflictName">复用并改为穿搭大标签</a-button>
        <a-button @click="renameInstead">另起一个名称</a-button>
        <a-button type="text" @click="skipConflictFolder">跳过此收藏夹</a-button>
      </div>
    </a-modal>

    <!-- 重命名全局标签 -->
    <a-modal
      v-model:visible="renameOpen"
      :title="`重命名标签「${renameRow?.tag_name ?? ''}」`"
      :width="'min(520px, calc(100vw - 32px))'"
      @ok="() => void submitRename()"
    >
      <a-input v-model="renameName" placeholder="新的标签名称" />
      <div v-if="renameImpact" class="rename-impact">
        <p>
          该标签当前被 <strong>{{ renameImpact.usage_count }}</strong> 个素材引用；
          映射到它的收藏夹：{{ renameImpact.mapped_folders.join('、') || '无' }}。
        </p>
        <p>重命名会影响所有引用该标签的素材与页面（包括非收藏夹来源的关联）。</p>
      </div>
      <a-checkbox v-model="renameConfirmed"> 我确认重命名会影响所有引用此标签的素材 </a-checkbox>
    </a-modal>
  </div>
</template>

<style scoped>
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.panel-hint {
  margin: 0 0 4px;
  font-size: 12px;
  color: var(--color-text-3);
}

.panel-pending {
  margin: 0;
  font-size: 12px;
  color: rgb(var(--warning-6));
}

.panel-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.panel-empty {
  padding: 32px 12px;
  text-align: center;
  color: var(--color-text-3);
}

.mapping-list {
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.mapping-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-2);
}

.mapping-row:last-child {
  border-bottom: none;
}

.mapping-name {
  min-width: 200px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.name-text {
  font-weight: 600;
}

.mapping-count {
  font-size: 12px;
  color: var(--color-text-3);
}

.mapping-tag {
  flex: 1;
  min-width: 160px;
  color: var(--color-text-2);
}

.mapping-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.conflict-text {
  margin: 0 0 16px;
  line-height: 1.6;
}

.conflict-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.rename-impact {
  margin: 12px 0;
  font-size: 12px;
  color: var(--color-text-3);
  line-height: 1.6;
}
</style>
