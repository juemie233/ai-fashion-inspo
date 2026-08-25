<script setup lang="ts">
/** 博主主页信息补全管理：缺失列表勾选 → 任务进度/结果 → 失败重试/跳过；含已跳过解除弹窗。
 *  逻辑在 useBloggerEnrich，本组件负责弹窗 UI 与打开/关闭接线。 */

import { watch } from 'vue'
import StatusTag from '@/components/common/StatusTag.vue'
import { useBloggerEnrich } from '@/composables/useBloggerEnrich'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  'update:visible': [v: boolean]
  finished: []
}>()

const enrich = useBloggerEnrich({ onFinished: () => emit('finished') })

/** 打开时拉取缺失博主列表并默认全选 */
watch(
  () => props.visible,
  (v) => {
    if (v) enrich.openEnrichModal()
  },
)

function closeEnrich() {
  enrich.closeEnrich()
  emit('update:visible', false)
  emit('finished')
}

function closeSkipManage() {
  enrich.skipManageOpen = false
}
</script>

<template>
  <!-- 补全博主主页信息弹窗（仅穿搭博主） -->
  <a-modal
    :visible="visible"
    title="补全博主主页信息"
    :width="560"
    :mask-closable="false"
    :footer="false"
    @cancel="closeEnrich"
  >
    <!-- 选择补全范围 -->
    <template v-if="!enrich.enrichTask">
      <p class="enrich-tip">
        为缺失主页信息（主页链接 / 平台用户
        ID）的小红书博主自动补全：优先本地互推，缺失时按小红书号搜索匹配。 单次最多补全 20
        位（防触发风控），完成后失败博主可单独重试。
      </p>
      <div class="enrich-list">
        <div v-for="b in enrich.missingItems" :key="b.id" class="enrich-row">
          <a-checkbox
            :model-value="enrich.selectedMissingIds.has(b.id)"
            @change="(v: unknown) => enrich.toggleMissing(b.id, Boolean(v))"
          >
            <span class="enrich-name">{{ b.name }}</span>
            <span class="enrich-xhs">{{ b.xhs_id || '无小红书号' }}</span>
          </a-checkbox>
        </div>
        <a-empty
          v-if="enrich.missingItems.length === 0"
          description="暂无可补全的博主"
          size="small"
        />
      </div>
      <div v-if="enrich.skippedItems.length > 0" style="margin-bottom: 10px">
        <a-button type="text" size="small" @click="enrich.skipManageOpen = true">
          已跳过 {{ enrich.skippedItems.length }} 位（查看 / 解除）
        </a-button>
      </div>
      <div class="enrich-actions">
        <a-button @click="closeEnrich">取消</a-button>
        <a-button
          type="primary"
          :loading="enrich.enrichBusy"
          :disabled="enrich.selectedMissingIds.size === 0"
          @click="enrich.startEnrich"
        >
          开始补全（{{ enrich.selectedMissingIds.size }}）
        </a-button>
      </div>
    </template>

    <!-- 任务进度与结果 -->
    <template v-else>
      <div class="enrich-progress">
        <StatusTag :status="enrich.enrichTask.status" />
        <a-progress
          v-if="['pending', 'running'].includes(enrich.enrichTask.status)"
          :percent="enrich.enrichTask.progress / 100"
        />
        <a-typography-text type="secondary" style="font-size: 12px">
          处理 {{ enrich.enrichTask.done }} / {{ enrich.enrichTask.total }}
        </a-typography-text>
      </div>

      <template v-if="!['pending', 'running'].includes(enrich.enrichTask.status)">
        <!-- 任务整体失败（如未导入 Cookie）：明确展示失败原因，引导处理 -->
        <a-alert
          v-if="enrich.enrichTask.status === 'failed' && enrich.enrichTask.error"
          type="error"
          style="margin: 12px 0"
          :message="`补全任务失败：${enrich.enrichTask.error}`"
        />
        <a-alert
          v-else
          :type="enrich.enrichFailed > 0 ? 'warning' : 'success'"
          style="margin: 12px 0"
          :message="
            `补全完成：成功 ${enrich.enrichUpdated} 位` +
            (enrich.enrichSkipped > 0
              ? `，跳过 ${enrich.enrichSkipped} 位（确定性无法获取）`
              : '') +
            (enrich.enrichFailed > 0 ? `，失败 ${enrich.enrichFailed} 位` : '')
          "
        />
        <!-- 自动跳过（确定性无法获取，已从缺失列表移除，可解除后重试） -->
        <div v-if="enrich.enrichSkippedItems.length > 0" class="enrich-failed enrich-skipped">
          <div
            v-for="item in enrich.enrichSkippedItems"
            :key="item.blogger_id"
            class="enrich-failed-row"
          >
            <span class="enrich-name">{{ item.name }}</span>
            <span class="enrich-reason">{{ item.reason || '未知原因' }}</span>
            <a-button
              size="mini"
              type="text"
              :loading="enrich.skipBusy"
              @click="enrich.unskipBloggers([item.blogger_id])"
            >
              解除跳过
            </a-button>
          </div>
        </div>
        <!-- 临时性失败（Cookie/登录墙/网络等，可重试或手动跳过） -->
        <div v-if="enrich.enrichFailedItems.length > 0" class="enrich-failed">
          <div
            v-for="item in enrich.enrichFailedItems"
            :key="item.blogger_id"
            class="enrich-failed-row"
          >
            <span class="enrich-name">{{ item.name }}</span>
            <span class="enrich-reason">{{ item.reason || '未知原因' }}</span>
            <a-button
              size="mini"
              type="text"
              :loading="enrich.skipBusy"
              @click="enrich.skipFailedBloggers([item.blogger_id], `跳过：${item.reason || ''}`)"
            >
              跳过
            </a-button>
          </div>
        </div>
        <div class="enrich-actions">
          <a-button
            v-if="enrich.enrichFailedItems.length > 0"
            type="secondary"
            :loading="enrich.enrichBusy"
            @click="enrich.retryFailed"
          >
            重试失败（{{ enrich.enrichFailedItems.length }}）
          </a-button>
          <a-button
            v-if="enrich.enrichFailedItems.length > 0"
            type="secondary"
            status="danger"
            :loading="enrich.skipBusy"
            @click="
              enrich.skipFailedBloggers(
                enrich.enrichFailedItems.map((r) => r.blogger_id),
                '手动跳过全部失败博主',
              )
            "
          >
            跳过全部失败（{{ enrich.enrichFailedItems.length }}）
          </a-button>
          <a-button type="primary" @click="closeEnrich">完成</a-button>
        </div>
      </template>
    </template>
  </a-modal>

  <!-- 已跳过补全管理弹窗（解除后重新纳入补全范围） -->
  <a-modal
    :visible="enrich.skipManageOpen"
    title="已跳过补全的博主"
    :width="520"
    :footer="false"
    @cancel="closeSkipManage"
  >
    <p class="enrich-tip">
      以下博主被标记为「跳过补全」（确定性无法获取主页信息）。解除后重新纳入补全范围，可再次尝试。
    </p>
    <div v-if="enrich.skippedItems.length > 0" class="enrich-list">
      <div
        v-for="item in enrich.skippedItems"
        :key="item.blogger_id"
        class="enrich-row enrich-skip-row"
      >
        <div class="enrich-skip-info">
          <span class="enrich-name">{{ item.name }}</span>
          <span class="enrich-xhs">{{ item.reason }}</span>
        </div>
        <a-button
          size="mini"
          type="text"
          :loading="enrich.skipBusy"
          @click="enrich.unskipBloggers([item.blogger_id])"
        >
          解除跳过
        </a-button>
      </div>
    </div>
    <a-empty v-else description="暂无已跳过的博主" size="small" />
    <div v-if="enrich.skippedItems.length > 0" class="enrich-actions">
      <a-button
        type="secondary"
        :loading="enrich.skipBusy"
        @click="enrich.unskipBloggers(enrich.skippedItems.map((s) => s.blogger_id))"
      >
        全部解除
      </a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.enrich-tip {
  font-size: 12px;
  color: #888;
  margin: 0 0 12px;
}

.enrich-list {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 14px;
}

.enrich-row {
  padding: 6px 0;
  border-bottom: 1px dashed #f2f3f5;
}

.enrich-row:last-child {
  border-bottom: none;
}

.enrich-name {
  font-size: 13px;
  margin-right: 8px;
}

.enrich-xhs {
  font-size: 12px;
  color: #999;
}

.enrich-progress {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}

.enrich-failed {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #fde2e2;
  background: #fff7f7;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 14px;
}

/* 自动跳过列表：浅黄色区分于红色失败列表 */
.enrich-failed.enrich-skipped {
  border-color: #fbe6c2;
  background: #fffbf3;
}

.enrich-failed-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}

.enrich-skip-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.enrich-skip-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.enrich-reason {
  color: #c0392b;
  font-size: 12px;
  text-align: right;
}

.enrich-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 4px;
}
</style>
