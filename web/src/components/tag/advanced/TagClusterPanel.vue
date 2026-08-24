<script setup lang="ts">
/** 聚类面板：参数配置 → 异步扫描 → 候选组列表 → 人工确认应用（合并 / 合并且保留别名）。 */

import { computed, onBeforeUnmount, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { applyClusters, asClusterResult, scanClusters } from '@/api/tagAdvanced'
import { useTaskPolling } from '@/composables/useTaskPolling'
import type { ClusterGroup } from '@/types/tagAdvanced'

const { task, pollTask, stopPolling } = useTaskPolling()

// ── 扫描参数 ──
const threshold = ref(0.75)
const useCooccurrenceBoost = ref(true)
const minGroupSize = ref(2)
const scanning = ref(false)

// ── 扫描结果 ──
const groups = ref<ClusterGroup[]>([])
const scannedAt = ref('')
const checkedIds = ref<string[]>([])

// ── 应用确认弹窗 ──
const applyVisible = ref(false)
const keepAsAlias = ref(true)
const applying = ref(false)

const hasResult = computed(() => groups.value.length > 0)

const running = computed(
  () => scanning.value || Boolean(task.value && ['pending', 'running'].includes(task.value.status)),
)

/** 勾选/取消单个候选组 */
function toggleGroup(groupId: string, checked: boolean) {
  if (checked) {
    if (!checkedIds.value.includes(groupId)) checkedIds.value.push(groupId)
  } else {
    checkedIds.value = checkedIds.value.filter((id) => id !== groupId)
  }
}

/** 提交聚类扫描 */
async function runScan() {
  if (running.value) return
  scanning.value = true
  try {
    const { task_id } = await scanClusters({
      threshold: threshold.value,
      use_cooccurrence_boost: useCooccurrenceBoost.value,
      min_group_size: minGroupSize.value,
    })
    pollTask(task_id, (result) => {
      const r = asClusterResult(result)
      if (r) {
        groups.value = r.groups
        checkedIds.value = []
        scannedAt.value = new Date().toLocaleString()
        Message.success(r.total ? `聚类完成：发现 ${r.total} 个候选组` : '未发现相似标签候选组')
      }
    })
  } catch (e) {
    Message.error(getApiErrorMessage(e, '提交聚类任务失败'))
  } finally {
    scanning.value = false
  }
}

/** 打开应用确认弹窗 */
function openApply() {
  if (!checkedIds.value.length) {
    Message.warning('请先勾选要应用的候选组')
    return
  }
  applyVisible.value = true
}

/** 应用勾选的候选组 */
async function confirmApply() {
  applying.value = true
  try {
    const selected = groups.value.filter((g) => checkedIds.value.includes(g.id))
    const data = await applyClusters({
      groups: selected.map((g) => ({
        group_id: g.id,
        target_tag_id: g.suggested_target.id,
        source_tag_ids: g.members.filter((m) => m.id !== g.suggested_target.id).map((m) => m.id),
        keep_as_alias: keepAsAlias.value,
      })),
    })
    Message.success(
      `已应用 ${data.applied} 组：合并 ${data.merged} 个标签，新建别名 ${data.aliases_created} 个` +
        (data.errors.length ? `，${data.errors.length} 处失败（详见提示）` : ''),
    )
    if (data.errors.length) {
      Message.warning(data.errors.map((e) => e.message).join('；'))
    }
    applyVisible.value = false
    runScan() // 重新扫描反映合并后的状态
  } catch (e) {
    Message.error(getApiErrorMessage(e, '应用候选组失败'))
  } finally {
    applying.value = false
  }
}

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="cluster-panel">
    <!-- 参数区 -->
    <div class="cluster-params">
      <div class="param-item">
        <span class="param-label">相似度阈值</span>
        <a-slider v-model="threshold" :min="0.6" :max="0.95" :step="0.05" style="width: 180px" />
        <span class="param-value">{{ threshold.toFixed(2) }}</span>
      </div>
      <div class="param-item">
        <span class="param-label">共现加成</span>
        <a-switch v-model="useCooccurrenceBoost" />
        <span class="param-hint">同素材共现 ≥2 次 +0.1</span>
      </div>
      <div class="param-item">
        <span class="param-label">最小组成员</span>
        <a-input-number v-model="minGroupSize" :min="2" :max="10" style="width: 90px" />
      </div>
      <a-button type="primary" :loading="running" @click="runScan">
        {{ groups.length ? '重新聚类' : '开始聚类' }}
      </a-button>
    </div>

    <!-- 候选组列表 -->
    <div v-if="!hasResult && !running" class="empty-tip">
      配置参数后点击「开始聚类」，系统将按名称相似度（可含共现加成）产出候选合并组，
      由你确认后再执行。
    </div>

    <div v-if="hasResult" class="cluster-groups">
      <div class="group-toolbar">
        <span>共 {{ groups.length }} 个候选组</span>
        <a-space>
          <a-button size="small" @click="checkedIds = groups.map((g) => g.id)">全选</a-button>
          <a-button size="small" @click="checkedIds = []">清空</a-button>
          <a-button size="small" type="primary" :disabled="!checkedIds.length" @click="openApply">
            应用选中组（{{ checkedIds.length }}）
          </a-button>
        </a-space>
      </div>

      <div
        v-for="g in groups"
        :key="g.id"
        class="group-card"
        :class="{ checked: checkedIds.includes(g.id) }"
      >
        <div class="group-head">
          <a-checkbox
            :model-value="checkedIds.includes(g.id)"
            @change="
              (v: boolean | Array<string | number | boolean>) => toggleGroup(g.id, v === true)
            "
          />
          <span class="group-reason">{{ g.reason }}</span>
          <span class="group-members-count">{{ g.members.length }} 个成员</span>
        </div>
        <div class="group-body">
          <a-tag
            v-for="m in g.members"
            :key="m.id"
            :color="m.id === g.suggested_target.id ? 'arcoblue' : 'gray'"
            size="medium"
          >
            {{ m.name }}（{{ m.usage_count }}）
            <template v-if="m.id === g.suggested_target.id">
              <span class="target-mark">建议保留</span>
            </template>
          </a-tag>
        </div>
      </div>
    </div>

    <!-- 应用确认弹窗 -->
    <a-modal v-model:visible="applyVisible" title="应用候选组" :footer="false" :width="420">
      <div class="apply-tip">
        将把勾选的 {{ checkedIds.length }} 个候选组内的源标签合并到建议主标签
        （使用次数最高者），删除源标签。
      </div>
      <div class="apply-option">
        <span>保留源标签名为别名（旧名继续归一化命中）</span>
        <a-switch v-model="keepAsAlias" />
      </div>
      <div class="apply-footer">
        <a-space>
          <a-button @click="applyVisible = false">取消</a-button>
          <a-button type="primary" :loading="applying" @click="confirmApply">确认应用</a-button>
        </a-space>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.cluster-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  overflow-y: auto;
}
.cluster-params {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  flex-shrink: 0;
}
.param-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.param-label {
  font-size: 13px;
  color: #374151;
}
.param-value {
  font-size: 13px;
  color: #2a78d6;
  font-weight: 600;
}
.param-hint {
  font-size: 12px;
  color: #9ca3af;
}
.empty-tip {
  padding: 40px;
  text-align: center;
  color: #9ca3af;
  font-size: 14px;
}
.cluster-groups {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.group-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: #6b7280;
}
.group-card {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  transition: all 0.15s;
}
.group-card.checked {
  border-color: #2a78d6;
  background: #f7faff;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.group-reason {
  font-size: 13px;
  color: #374151;
}
.group-members-count {
  font-size: 12px;
  color: #9ca3af;
}
.group-body {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 24px;
}
.target-mark {
  margin-left: 4px;
  font-size: 11px;
  color: #2a78d6;
}
.apply-tip {
  font-size: 13px;
  color: #6b7280;
  line-height: 1.6;
  margin-bottom: 12px;
}
.apply-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  padding: 8px 0;
}
.apply-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
