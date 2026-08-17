<script setup lang="ts">
/** 近似重复检测：感知哈希分组候选 + 并排预览 + 人工确认删除。 */

import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  fetchNearDuplicates,
  type NearDuplicateGroup,
  type NearDuplicateFile,
  type NearDuplicateResult,
} from '@/api/admin'
import { getFileUrl } from '@/api/inspirations'
import { formatSize } from '@/utils/format'

const emit = defineEmits<{
  (e: 'delete-selected', ids: string[]): void
}>()

const message = useMessage()

const threshold = ref(32)
const limit = ref(1000)
const scanning = ref(false)
const result = ref<NearDuplicateResult | null>(null)
/** 待删除的素材 ID 集合（默认：每组除建议保留者外的全部） */
const toDelete = ref<Set<string>>(new Set())

const thresholdOptions = [
  { label: '严格（16，仅极相似）', value: 16 },
  { label: '标准（32，推荐）', value: 32 },
  { label: '宽松（64，含轻微差异）', value: 64 },
]

const limitOptions = [
  { label: '随机 500 张', value: 500 },
  { label: '随机 1000 张', value: 1000 },
  { label: '随机 2000 张', value: 2000 },
  { label: '随机 5000 张', value: 5000 },
]

const groups = computed(() => result.value?.groups ?? [])
/** 待删除总数 */
const deleteCount = computed(() => toDelete.value.size)
/** 待回收空间（待删除文件大小之和） */
const deleteBytes = computed(() => {
  let sum = 0
  for (const g of groups.value) {
    for (const f of g.files) {
      if (toDelete.value.has(f.id)) sum += f.size_bytes
    }
  }
  return sum
})

async function scan() {
  scanning.value = true
  result.value = null
  toDelete.value = new Set()
  try {
    result.value = await fetchNearDuplicates(limit.value, threshold.value)
    // 默认勾选每组除建议保留者外的全部冗余文件
    const ids = new Set<string>()
    for (const g of result.value.groups) {
      for (const f of g.files) {
        if (f.id !== g.keeper_id) ids.add(f.id)
      }
    }
    toDelete.value = ids
    if (result.value.groups.length === 0) {
      message.success(
        `已随机扫描 ${result.value.scanned} 张，未发现近似重复（可再次扫描覆盖其他素材）`,
      )
    }
  } catch {
    message.error('近似重复扫描失败')
  } finally {
    scanning.value = false
  }
}

/** 切换单个文件是否删除，保证每组至少保留一个 */
function toggleDelete(group: NearDuplicateGroup, file: NearDuplicateFile) {
  const next = new Set(toDelete.value)
  if (next.has(file.id)) {
    next.delete(file.id)
  } else {
    // 若勾选后该组全部待删，则禁止（至少保留一个）
    const wouldDeleteAll = group.files.every((f) => f.id === file.id || next.has(f.id))
    if (wouldDeleteAll) {
      message.warning('每组至少保留一个素材')
      return
    }
    next.add(file.id)
  }
  toDelete.value = next
}

/** 确认删除：把待删除 ID 交给父组件（复用批量删除任务 + 审计留痕） */
function confirmDelete() {
  if (toDelete.value.size === 0) {
    message.warning('请先勾选要删除的素材')
    return
  }
  emit('delete-selected', [...toDelete.value])
  // 删除任务提交后清空勾选；如需查看最新结果请重新扫描
  toDelete.value = new Set()
}

function fileUrl(f: NearDuplicateFile): string {
  return getFileUrl(f.thumbnail_path || f.file_path)
}
</script>

<template>
  <n-card title="近似重复检测" size="small" style="margin-bottom: 24px">
    <template #header-extra>
      <n-space align="center">
        <n-select
          v-model:value="threshold"
          :options="thresholdOptions"
          size="small"
          style="width: 180px"
        />
        <n-select v-model:value="limit" :options="limitOptions" size="small" style="width: 130px" />
        <n-button size="small" type="primary" :loading="scanning" @click="scan"
          >扫描近似重复</n-button
        >
      </n-space>
    </template>

    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      基于感知哈希识别「视觉相似但字节不同」的图片（不同压缩/缩放/水印），
      <b>全库随机抽样</b>，每次扫描覆盖不同素材；哈希首次计算后自动缓存，
      之后扫描秒级返回。仅列出候选，需人工确认后删除。
    </p>

    <!-- 扫描结果汇总 -->
    <n-alert v-if="result && result.groups.length === 0" type="success" style="margin-bottom: 12px">
      已随机扫描 {{ result.scanned }} / {{ result.total }} 张，未发现近似重复
      <template v-if="result.truncated">（仅覆盖本次抽样，可再次扫描发现其他素材）</template>
    </n-alert>

    <template v-if="groups.length > 0">
      <p style="color: #f0a020; margin-bottom: 12px">
        ⚠️ 发现 {{ groups.length }} 组近似重复，本次随机扫描 {{ result?.scanned }} /
        {{ result?.total }} 张
        <template v-if="result?.truncated">（存在未覆盖素材，可再次扫描）</template>
      </p>

      <!-- 哈希缓存进度 -->
      <n-alert
        v-if="result && result.cached_total < result.total"
        type="info"
        :bordered="false"
        style="margin-bottom: 12px"
      >
        感知哈希缓存 {{ result.cached_total }} / {{ result.total }} 张（本次新增
        {{ result.backfilled }} 张），缓存完备后扫描无需重新解码图片
      </n-alert>

      <div v-for="(group, gi) in groups" :key="group.rep_phash" class="nd-group">
        <div class="nd-group-head">
          <n-tag type="info" size="tiny">第 {{ gi + 1 }} 组 · {{ group.files.length }} 张</n-tag>
          <span class="nd-wasted">约可回收 {{ formatSize(group.wasted_bytes) }}</span>
        </div>
        <div class="nd-files">
          <div
            v-for="f in group.files"
            :key="f.id"
            class="nd-file"
            :class="{ 'nd-delete': toDelete.has(f.id) }"
            @click="toggleDelete(group, f)"
          >
            <img :src="fileUrl(f)" :alt="f.file_path" loading="lazy" />
            <div v-if="f.id === group.keeper_id" class="nd-keeper-badge">建议保留</div>
            <div class="nd-file-meta">
              <span>{{ formatSize(f.size_bytes) }}</span>
              <n-tag v-if="f.is_favorite" size="tiny" type="error" :bordered="false">♥</n-tag>
            </div>
            <div class="nd-checkbox" :class="{ checked: toDelete.has(f.id) }">
              <span v-if="toDelete.has(f.id)">✕</span>
            </div>
          </div>
        </div>
      </div>

      <div class="nd-actions">
        <span>已勾选删除 {{ deleteCount }} 个，可回收 {{ formatSize(deleteBytes) }}</span>
        <n-popconfirm @positive-click="confirmDelete">
          <template #trigger>
            <n-button type="error" :disabled="deleteCount === 0">删除勾选素材</n-button>
          </template>
          将物理删除 {{ deleteCount }} 个勾选素材，不可撤销！确定继续？
        </n-popconfirm>
      </div>
    </template>

    <n-empty v-else-if="!scanning" description="点击「扫描近似重复」开始检测" size="small" />
  </n-card>
</template>

<style scoped>
.nd-group {
  border: 1px solid #eee;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 14px;
}
.nd-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.nd-wasted {
  font-size: 12px;
  color: #f0a020;
}
.nd-files {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.nd-file {
  position: relative;
  width: 120px;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  transition:
    border-color 0.15s,
    opacity 0.15s;
}
.nd-file img {
  width: 120px;
  height: 150px;
  object-fit: cover;
  display: block;
  background: #f5f5f5;
}
.nd-file.nd-delete {
  border-color: #d03050;
}
.nd-file.nd-delete img {
  opacity: 0.55;
}
.nd-keeper-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(24, 160, 88, 0.9);
  color: #fff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
}
.nd-file-meta {
  position: absolute;
  bottom: 6px;
  left: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
  color: #fff;
  font-size: 11px;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}
.nd-checkbox {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.9);
  border: 2px solid #ccc;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #fff;
}
.nd-checkbox.checked {
  background: #d03050;
  border-color: #d03050;
}
.nd-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 4px;
  color: #666;
  font-size: 13px;
}
</style>
