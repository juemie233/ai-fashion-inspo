<script setup lang="ts">
/** 近似重复检测：感知哈希分组候选 + 弹窗逐组左右对比 + 人工确认删除。
 *
 * 扫描出近似重复组后自动打开对比弹窗：左右并排展示每组前两张素材，
 * 由用户决定保留哪一张（或都保留跳过）；全部处理完统一提交删除任务，
 * 物理删除冗余素材（复用批量删除任务 + 审计留痕）。
 */

import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  fetchNearDuplicates,
  type NearDuplicateFile,
  type NearDuplicateGroup,
  type NearDuplicateResult,
} from '@/api/admin'
import { getFileUrl } from '@/api/inspirations'
import { formatSize } from '@/utils/format'
import { collectIdsToDelete } from '@/utils/nearDup'

const emit = defineEmits<{
  (e: 'delete-selected', ids: string[]): void
}>()

const message = useMessage()

const threshold = ref(32)
const limit = ref(1000)
const scanning = ref(false)
const result = ref<NearDuplicateResult | null>(null)

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

// ── 弹窗逐组对比状态 ──
const showDupModal = ref(false)
/** 待处理的重复组队列（按扫描结果顺序） */
const dupGroups = ref<NearDuplicateGroup[]>([])
/** 当前组下标（0 起） */
const dupIndex = ref(0)
/** 累计决定删除的素材 ID 集合 */
const deletingIds = ref<Set<string>>(new Set())
/** 全部处理完成（进入提交确认视图） */
const allDone = ref(false)
/** 提交删除任务中 */
const submitting = ref(false)

/** 当前组 */
const currentGroup = computed<NearDuplicateGroup | null>(
  () => dupGroups.value[dupIndex.value] ?? null,
)
/** 当前组左边素材（组内第一张，评分最高者） */
const leftFile = computed<NearDuplicateFile | null>(
  () => currentGroup.value?.files[0] ?? null,
)
/** 当前组右边素材（组内第二张） */
const rightFile = computed<NearDuplicateFile | null>(
  () => currentGroup.value?.files[1] ?? null,
)
/** 已决定删除数量 */
const deleteCount = computed(() => deletingIds.value.size)

/** 建议保留素材（评分最高者）ID 前 8 位提示 */
const keeperHint = computed<string>(() => {
  const g = currentGroup.value
  if (!g) return ''
  const keeper = g.files.find((f) => f.id === g.keeper_id) ?? g.files[0]
  return keeper ? `${keeper.id.slice(0, 8)}…` : ''
})

async function scan() {
  scanning.value = true
  result.value = null
  try {
    result.value = await fetchNearDuplicates(limit.value, threshold.value)
    if (result.value.groups.length === 0) {
      message.success(
        `已随机扫描 ${result.value.scanned} 张，未发现近似重复（可再次扫描覆盖其他素材）`,
      )
    } else {
      // 发现重复组 → 自动打开弹窗逐组处理
      openDupModal()
    }
  } catch {
    message.error('近似重复扫描失败')
  } finally {
    scanning.value = false
  }
}

/** 打开逐组对比弹窗 */
function openDupModal() {
  dupGroups.value = groups.value.map((g) => g)
  dupIndex.value = 0
  deletingIds.value = new Set()
  allDone.value = false
  showDupModal.value = true
}

/** 关闭弹窗（未提交的删除决定作废） */
function closeDupModal() {
  showDupModal.value = false
  dupGroups.value = []
}

/** 记录删除决定并切到下一组（或进入完成视图） */
function decideDelete(idsToDelete: string[]) {
  for (const id of idsToDelete) {
    deletingIds.value.add(id)
  }
  if (dupIndex.value < dupGroups.value.length - 1) {
    dupIndex.value += 1
  } else {
    allDone.value = true // 全部处理完，进入提交确认视图
  }
}

/** 保留左边：删除该组其余全部素材 */
function keepLeft() {
  const g = currentGroup.value
  if (!g) return
  decideDelete(collectIdsToDelete(g, 'keep-left'))
}

/** 保留右边：删除该组除右图外的全部素材 */
function keepRight() {
  const g = currentGroup.value
  if (!g) return
  decideDelete(collectIdsToDelete(g, 'keep-right'))
}

/** 都保留（跳过本组） */
function skipGroup() {
  const g = currentGroup.value
  if (!g) return
  decideDelete(collectIdsToDelete(g, 'skip'))
}

/** 提交删除：把全部待删 ID 交给父组件（批量删除任务 + 审计留痕） */
async function confirmSubmit() {
  if (deletingIds.value.size === 0) {
    message.info('没有需要删除的素材')
    closeDupModal()
    return
  }
  submitting.value = true
  try {
    emit('delete-selected', [...deletingIds.value])
    message.success(`已提交删除任务：${deletingIds.value.size} 个冗余素材（后台物理删除）`)
    showDupModal.value = false
    dupGroups.value = []
    // 提交后重新扫描，刷新当前列表状态
    await scan()
  } catch (e) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail
    message.error(detail || '提交删除失败')
  } finally {
    submitting.value = false
  }
}

function fileUrl(f: NearDuplicateFile): string {
  return getFileUrl(f.thumbnail_path || f.file_path)
}

/** 上传时间展示：MM-DD HH:mm（无时间返回 '-'） */
function fmtTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 收藏标记展示 */
function favoriteLabel(f: NearDuplicateFile): string {
  return f.is_favorite ? ' ♥' : ''
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

      <n-space align="center" style="margin-bottom: 12px">
        <n-button type="primary" @click="openDupModal">开始逐组对比处理（{{ groups.length }} 组）</n-button>
        <n-text depth="3" style="font-size: 12px">
          弹窗中左右对比每组素材，选择保留哪一张；冗余素材将物理删除（不可恢复）
        </n-text>
      </n-space>
    </template>

    <n-empty v-else-if="!scanning" description="点击「扫描近似重复」开始检测" size="small" />
  </n-card>

  <!-- 近似重复逐组对比弹窗：左右并排大图 + 保留决策 -->
  <n-modal
    v-model:show="showDupModal"
    preset="card"
    title="近似重复素材对比"
    style="width: 92%; max-width: 1200px"
    :bordered="false"
    :show-close="!submitting"
    :mask-closable="false"
    :close-on-esc="false"
    @close="closeDupModal"
  >
    <!-- 逐组决策视图 -->
    <template v-if="!allDone && currentGroup">
      <div class="dup-step">
        第 {{ dupIndex + 1 }} / {{ dupGroups.length }} 组 · 本组共 {{ currentGroup?.files.length ?? 0 }} 张近似重复
        <n-tag size="tiny" type="info" style="margin-left: 8px">建议保留：{{ keeperHint }}</n-tag>
      </div>

      <div class="dup-compare">
        <!-- 左图 -->
        <div class="dup-side">
          <div class="dup-side-label">保留左边</div>
          <div class="dup-img-wrap" v-if="leftFile">
            <img :src="fileUrl(leftFile)" :alt="leftFile.file_path" />
          </div>
          <div class="dup-side-meta" v-if="leftFile">
            ID {{ leftFile.id.slice(0, 8) }} · 上传 {{ fmtTime(leftFile.created_at)
            }}<template v-if="favoriteLabel(leftFile)">{{ favoriteLabel(leftFile) }}</template>
            <br />
            {{ formatSize(leftFile.size_bytes) }}
          </div>
        </div>

        <div class="dup-vs">VS</div>

        <!-- 右图 -->
        <div class="dup-side">
          <div class="dup-side-label">保留右边</div>
          <div class="dup-img-wrap" v-if="rightFile">
            <img :src="fileUrl(rightFile)" :alt="rightFile.file_path" />
          </div>
          <div class="dup-side-meta" v-if="rightFile">
            ID {{ rightFile.id.slice(0, 8) }} · 上传 {{ fmtTime(rightFile.created_at)
            }}<template v-if="favoriteLabel(rightFile)">{{ favoriteLabel(rightFile) }}</template>
            <br />
            {{ formatSize(rightFile.size_bytes) }}
          </div>
        </div>
      </div>

      <p class="dup-hint">
        组内共有 {{ currentGroup?.files.length ?? 0 }} 张，此处对比前两张；选择保留一张，其余将
        <strong>永久删除</strong>（文件与记录不可恢复）。
      </p>

      <div class="dup-actions">
        <n-button type="primary" @click="keepLeft">保留左边</n-button>
        <n-button type="warning" @click="keepRight">保留右边</n-button>
        <n-button quaternary @click="skipGroup">都保留（跳过本组）</n-button>
      </div>

      <div class="dup-progress">已决定删除 {{ deleteCount }} 个素材</div>
    </template>

    <!-- 全部处理完：提交确认视图 -->
    <template v-else-if="allDone">
      <n-result status="info" title="对比完成" style="margin: 8px 0">
        <template #description>
          共处理 {{ dupGroups.length }} 组近似重复，决定删除
          <b>{{ deleteCount }}</b> 个冗余素材（物理删除，不可恢复）
        </template>
        <template #footer>
          <n-space justify="center">
            <n-popconfirm @positive-click="confirmSubmit">
              <template #trigger>
                <n-button type="error" :loading="submitting" :disabled="deleteCount === 0">
                  确认提交删除（{{ deleteCount }} 个）
                </n-button>
              </template>
              将物理删除 {{ deleteCount }} 个素材（文件与记录不可恢复），确定继续？
            </n-popconfirm>
            <n-button :disabled="submitting" @click="closeDupModal">关闭（不删除）</n-button>
          </n-space>
        </template>
      </n-result>
    </template>
  </n-modal>
</template>

<style scoped>
/* 逐组步骤提示 */
.dup-step {
  margin-bottom: 12px;
  font-weight: 600;
  color: #333;
}

/* 左右对比布局：等宽两列 + 中间 VS */
.dup-compare {
  display: flex;
  align-items: stretch;
  gap: 12px;
}

.dup-side {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.dup-side-label {
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: #555;
  margin-bottom: 8px;
}

/* 图片容器：高度 ≥70vh，图片等比完整显示（不裁切） */
.dup-img-wrap {
  height: 70vh;
  min-height: 420px;
  background: #111;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dup-img-wrap img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}

.dup-side-meta {
  margin-top: 8px;
  text-align: center;
  font-size: 12px;
  color: #888;
  line-height: 1.6;
}

.dup-vs {
  align-self: center;
  font-weight: 800;
  color: #bbb;
  font-size: 18px;
  padding: 0 2px;
  flex-shrink: 0;
}

.dup-hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: #999;
  line-height: 1.8;
}

.dup-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
}

.dup-progress {
  text-align: center;
  font-size: 12px;
  color: #888;
  margin-top: 12px;
}
</style>
