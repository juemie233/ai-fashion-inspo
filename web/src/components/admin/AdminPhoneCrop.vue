<script setup lang="ts">
/** 手机图剪裁面板：扫描候选（只读预览）→ 手动勾选确认 → 执行裁剪 → 自动入队向量回填。 */

import { ref, computed } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import { getFileUrl } from '@/api/inspirations'

const message = useMessage()

/** 裁剪模式：auto 黑边自动检测 / ratio 固定比例 */
const mode = ref<'auto' | 'ratio'>('auto')
/** 顶部/底部裁剪比例（百分比，仅 ratio 模式生效） */
const cropTop = ref(3)
const cropBottom = ref(5)
/** 单次最多返回候选数 */
const limit = ref(200)

const scanning = ref(false)
const cropping = ref(false)

/** 扫描候选 */
interface CropCandidate {
  id: string
  file_path: string
  width: number
  height: number
  ratio: number
  crop_top: number
  crop_bottom: number
  auto_ok: boolean
  note: string | null
}

/** 候选网格与勾选状态（默认全选；auto 检测失败的默认不勾选） */
const candidates = ref<CropCandidate[]>([])
const checkedIds = ref<Set<string>>(new Set())

/** 执行结果 */
interface CropApplyResult {
  processed: number
  skipped: Array<{ id: string; reason: string }>
  backup_dir: string | null
  vector_task_id: number | null
}
const result = ref<CropApplyResult | null>(null)

const scannedTotal = ref(0)
const checkedCount = computed(() => checkedIds.value.size)

/** 扫描候选：只读预览，不修改任何数据 */
async function handleScan() {
  scanning.value = true
  result.value = null
  try {
    const { data } = await apiClient.post<{ total: number; items: CropCandidate[] }>(
      '/admin/crop-phone-screenshots/scan',
      {
        mode: mode.value,
        crop_top: cropTop.value / 100,
        crop_bottom: cropBottom.value / 100,
        limit: limit.value,
      },
    )
    scannedTotal.value = data.total
    candidates.value = data.items
    // 默认全选（auto 检测失败的条目不勾选，供人工复核）
    const ids = new Set<string>()
    for (const c of data.items) {
      if (c.auto_ok) ids.add(c.id)
    }
    checkedIds.value = ids
    if (data.items.length === 0) {
      message.info(data.total === 0 ? '没有可裁剪的竖屏截图素材' : `候选超过上限，仅显示前 ${limit.value} 张`)
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '扫描失败')
  } finally {
    scanning.value = false
  }
}

/** 切换单个候选勾选 */
function toggleCheck(id: string) {
  const next = new Set(checkedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checkedIds.value = next
}

/** 全选 / 取消全选（只作用于 auto 检测成功的条目） */
function toggleAll() {
  if (checkedCount.value === candidates.value.length) {
    checkedIds.value = new Set()
  } else {
    checkedIds.value = new Set(candidates.value.filter((c) => c.auto_ok).map((c) => c.id))
  }
}

/** 确认裁剪：仅处理勾选的素材，成功后自动入队向量回填 */
async function handleApply() {
  if (checkedCount.value === 0) {
    message.warning('请先勾选要裁剪的素材')
    return
  }
  cropping.value = true
  result.value = null
  try {
    const { data } = await apiClient.post<CropApplyResult>('/admin/crop-phone-screenshots/apply', {
      ids: [...checkedIds.value],
      mode: mode.value,
      crop_top: cropTop.value / 100,
      crop_bottom: cropBottom.value / 100,
    })
    result.value = data
    if (data.processed > 0) {
      message.success(`裁剪完成：成功 ${data.processed} 张，已入队向量回填`)
      // 裁剪成功后刷新候选：已处理的素材不再出现在候选列表
      await handleScan()
    } else if (data.skipped.length > 0) {
      message.warning(`裁剪完成：成功 0 张（${data.skipped.length} 张跳过）`)
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '裁剪失败')
  } finally {
    cropping.value = false
  }
}

/** 候选缩略图地址 */
function thumbUrl(c: CropCandidate): string {
  return getFileUrl(c.file_path)
}

/** 裁剪比例展示文案 */
function cropLabel(c: CropCandidate): string {
  return `${(c.crop_top * 100).toFixed(1)}% / ${(c.crop_bottom * 100).toFixed(1)}%`
}
</script>

<template>
  <n-card title="手机图剪裁" size="small" style="margin-bottom: 24px">
    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      扫描手动上传素材中的手机全屏截图（仅「手动上传 + 竖屏 高/宽 ≥ 1.75」），
      <b>人工勾选确认后</b>执行裁剪：裁掉顶部状态栏、底部导航栏等多余区域。
      原图自动备份到 <code>storage/_crop_backup/</code>，裁剪成功后自动入队向量回填；
      标签/收藏等信息不动。
    </p>

    <n-form label-placement="left" label-width="110" size="small" style="max-width: 560px">
      <n-form-item label="裁剪模式">
        <n-radio-group v-model:value="mode">
          <n-radio-button value="auto">自动检测黑边（推荐）</n-radio-button>
          <n-radio-button value="ratio">固定比例</n-radio-button>
        </n-radio-group>
      </n-form-item>

      <template v-if="mode === 'ratio'">
        <n-form-item label="顶部裁剪">
          <n-input-number v-model:value="cropTop" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </n-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999">默认 3%（状态栏区域）</span>
        </n-form-item>
        <n-form-item label="底部裁剪">
          <n-input-number v-model:value="cropBottom" :min="0" :max="40" style="width: 120px">
            <template #suffix>%</template>
          </n-input-number>
          <span style="margin-left: 8px; font-size: 12px; color: #999">默认 5%（底部导航栏/手势条）</span>
        </n-form-item>
      </template>

      <n-form-item label="数量上限">
        <n-input-number v-model:value="limit" :min="1" :max="1000" style="width: 120px" />
        <span style="margin-left: 8px; font-size: 12px; color: #999">单次最多扫描的候选数</span>
      </n-form-item>

      <n-form-item label=" ">
        <n-button type="primary" :loading="scanning" @click="handleScan">
          {{ scanning ? '扫描中...' : '扫描候选' }}
        </n-button>
        <span v-if="mode === 'auto'" style="margin-left: 12px; font-size: 12px; color: #f0a020">
          自动检测失败（浅色背景/复杂布局）的素材默认不勾选
        </span>
      </n-form-item>
    </n-form>

    <!-- 候选网格：人工勾选确认 -->
    <template v-if="candidates.length > 0">
      <n-divider style="margin: 12px 0" />
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 10px">
        <n-checkbox
          :checked="checkedCount > 0 && checkedCount === candidates.length"
          :indeterminate="checkedCount > 0 && checkedCount < candidates.length"
          @update:checked="toggleAll"
        />
        <span style="font-size: 13px">
          已勾选 <b>{{ checkedCount }}</b> / {{ candidates.length }} 张（共扫描 {{ scannedTotal }} 张候选）
        </span>
        <n-button size="small" type="primary" :loading="cropping" :disabled="checkedCount === 0" @click="handleApply">
          {{ cropping ? '裁剪中...' : `确认裁剪（${checkedCount} 张）` }}
        </n-button>
      </div>

      <div class="crop-grid">
        <div
          v-for="c in candidates"
          :key="c.id"
          class="crop-item"
          :class="{ checked: checkedIds.has(c.id), failed: !c.auto_ok }"
          @click="toggleCheck(c.id)"
        >
          <img :src="thumbUrl(c)" :alt="c.id" loading="lazy" />
          <div class="crop-meta">
            <span>{{ c.width }}×{{ c.height }} · 比例 {{ c.ratio }}</span>
            <span>裁剪 {{ cropLabel(c) }}</span>
            <n-tag v-if="!c.auto_ok" size="tiny" type="error" :bordered="false">{{ c.note }}</n-tag>
          </div>
          <div class="crop-check" :class="{ checked: checkedIds.has(c.id) }">
            <span v-if="checkedIds.has(c.id)">✓</span>
          </div>
        </div>
      </div>
    </template>

    <!-- 执行结果 -->
    <template v-if="result">
      <n-divider style="margin: 12px 0" />
      <n-alert
        :type="result.processed > 0 ? 'success' : 'warning'"
        style="margin-bottom: 8px"
      >
        成功裁剪 {{ result.processed }} 张 · 跳过 {{ result.skipped.length }} 张
        <template v-if="result.vector_task_id">
          · 已入队向量回填任务 #{{ result.vector_task_id }}（worker 执行）
        </template>
        <template v-if="result.backup_dir">
          · 原图备份：<code>{{ result.backup_dir }}</code>
        </template>
      </n-alert>

      <n-collapse v-if="result.skipped.length > 0" style="margin-top: 8px">
        <n-collapse-item title="跳过明细" name="skipped">
          <ul style="font-size: 12px; color: #666; margin: 0; padding-left: 18px; max-height: 240px; overflow-y: auto">
            <li v-for="s in result.skipped" :key="s.id">
              {{ s.id.slice(0, 8) }}… — {{ s.reason }}
            </li>
          </ul>
        </n-collapse-item>
      </n-collapse>
    </template>
  </n-card>
</template>

<style scoped>
.crop-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 10px;
}

.crop-item {
  position: relative;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  transition: border-color 0.15s, opacity 0.15s;
}

.crop-item img {
  width: 100%;
  height: 170px;
  object-fit: cover;
  display: block;
  background: #f5f5f5;
}

.crop-item.checked {
  border-color: #18a058;
}

.crop-item.failed {
  opacity: 0.6;
}

.crop-meta {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 4px 6px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
}

.crop-check {
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

.crop-check.checked {
  background: #18a058;
  border-color: #18a058;
}
</style>
