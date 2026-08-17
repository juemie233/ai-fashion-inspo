<script setup lang="ts">
/** 手机图剪裁面板：一键裁剪手动上传素材中的手机全屏截图（状态栏/底部导航栏等多余区域）。 */

import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'

const message = useMessage()

/** 裁剪模式：auto 黑边自动检测 / ratio 固定比例 */
const mode = ref<'auto' | 'ratio'>('auto')
/** 顶部/底部裁剪比例（百分比，仅 ratio 模式生效） */
const cropTop = ref(3)
const cropBottom = ref(5)
/** 单次最多处理候选数 */
const limit = ref(200)

const cropping = ref(false)

interface CropResult {
  scanned: number
  processed: number
  skipped: Array<{ id: string; reason: string }>
  backup_dir: string | null
  vector_task_id: number | null
}

const result = ref<CropResult | null>(null)

/** 一键裁剪：扫描手动上传的竖屏截图候选并立即执行裁剪 */
async function handleCrop() {
  cropping.value = true
  result.value = null
  try {
    const { data } = await apiClient.post<CropResult>('/admin/crop-phone-screenshots', {
      mode: mode.value,
      crop_top: cropTop.value / 100,
      crop_bottom: cropBottom.value / 100,
      limit: limit.value,
    })
    result.value = data
    if (data.processed > 0) {
      message.success(`裁剪完成：成功 ${data.processed} 张`)
    } else if (data.scanned === 0) {
      message.info('没有可裁剪的竖屏截图素材')
    } else {
      message.warning(`裁剪完成：成功 0 张（${data.skipped.length} 张跳过）`)
    }
  } catch (e: any) {
    message.error(e.response?.data?.detail || '裁剪失败')
  } finally {
    cropping.value = false
  }
}
</script>

<template>
  <n-card title="手机图剪裁" size="small" style="margin-bottom: 24px">
    <p style="color: #999; font-size: 12px; margin: 0 0 12px">
      一键裁剪手动上传素材中的手机全屏截图：裁掉顶部状态栏、底部导航栏等与穿搭无关的区域。
      仅处理「手动上传 + 竖屏（高/宽 ≥ 1.75）」的图片，原图自动备份到
      <code>storage/_crop_backup/</code>，可手动恢复；标签/收藏等信息不动。
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
        <span style="margin-left: 8px; font-size: 12px; color: #999">单次最多处理的候选数</span>
      </n-form-item>

      <n-form-item label=" ">
        <n-button type="primary" :loading="cropping" @click="handleCrop">
          {{ cropping ? '裁剪中...' : '一键裁剪' }}
        </n-button>
        <span v-if="mode === 'auto'" style="margin-left: 12px; font-size: 12px; color: #f0a020">
          自动检测失败（浅色背景/复杂布局）的素材会自动跳过
        </span>
      </n-form-item>
    </n-form>

    <!-- 裁剪结果 -->
    <template v-if="result">
      <n-alert
        :type="result.processed > 0 ? 'success' : result.scanned === 0 ? 'info' : 'warning'"
        style="margin-bottom: 8px"
      >
        扫描 {{ result.scanned }} 个候选 · 成功裁剪 {{ result.processed }} 张 · 跳过
        {{ result.skipped.length }} 张
        <template v-if="result.backup_dir">
          · 原图备份：<code>{{ result.backup_dir }}</code>
        </template>
        <template v-if="result.vector_task_id">
          · 已入队向量回填任务 #{{ result.vector_task_id }}
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
