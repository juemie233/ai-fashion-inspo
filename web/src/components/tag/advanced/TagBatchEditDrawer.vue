<script setup lang="ts">
/** 批量高级编辑抽屉：规则构建（多规则按序）→ dry-run 预览 → 确认执行。 */

import { computed, ref, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { batchEditTags } from '@/api/tagAdvanced'
import TagRuleForm from './TagRuleForm.vue'
import type { BatchEditPreviewItem, BatchEditResult, BatchEditRule } from '@/types/tagAdvanced'

const visible = defineModel<boolean>('visible', { required: true })

const props = defineProps<{
  initialTagIds?: number[]
  initialCategory?: string
}>()

// ── 规则列表 ──
const rules = ref<BatchEditRule[]>([])

function emptyRule(): BatchEditRule {
  return { type: 'regex_replace', pattern: '', replacement: '', scope: {} }
}

function addRule() {
  rules.value.push(emptyRule())
}

function removeRule(index: number) {
  rules.value.splice(index, 1)
}

function onRuleChange(index: number, rule: BatchEditRule) {
  rules.value[index] = rule
}

// ── 打开时按初始范围预填首条规则 ──
watch(visible, (v) => {
  if (!v) {
    preview.value = []
    previewResult.value = null
    return
  }
  if (rules.value.length === 0) {
    const first = emptyRule()
    if (props.initialTagIds?.length) {
      first.scope = { tag_ids: props.initialTagIds }
    } else if (props.initialCategory) {
      first.scope = { category: props.initialCategory }
    }
    rules.value = [first]
  }
})

// ── 预览 / 执行 ──
const preview = ref<BatchEditPreviewItem[]>([])
const previewResult = ref<BatchEditResult | null>(null)
const previewing = ref(false)
const executing = ref(false)

const ACTION_LABELS: Record<string, string> = { rename: '改名', merge: '合并', skip: '跳过' }

async function runPreview() {
  if (!rules.value.length) {
    Message.warning('请先添加规则')
    return
  }
  previewing.value = true
  try {
    const data = await batchEditTags({ dry_run: true, rules: rules.value })
    preview.value = data.preview ?? []
    previewResult.value = data
    if (!preview.value.length) {
      Message.info('没有标签会发生变化')
    }
  } catch (e) {
    Message.error(getApiErrorMessage(e, '预览失败'))
  } finally {
    previewing.value = false
  }
}

function confirmExecute() {
  if (!previewResult.value || !preview.value.length) {
    Message.warning('请先预览确认影响范围')
    return
  }
  const s = previewResult.value.summary
  Modal.confirm({
    title: '确认执行',
    content: `将执行：改名 ${s.renamed} 个、合并 ${s.merged} 个、跳过 ${s.skipped} 个。确定继续吗？`,
    onOk: async () => {
      executing.value = true
      try {
        const data = await batchEditTags({ dry_run: false, rules: rules.value })
        const sum = data.summary
        Message.success(
          `执行完成：改名 ${sum.renamed} 个、合并 ${sum.merged} 个` +
            (data.batch_id ? `（批次 ${data.batch_id}）` : ''),
        )
        if (data.errors?.length) {
          Message.warning(data.errors.map((e) => e.message).join('；'))
        }
        preview.value = []
        previewResult.value = null
        rules.value = []
        visible.value = false
      } catch (e) {
        Message.error(getApiErrorMessage(e, '执行失败'))
      } finally {
        executing.value = false
      }
    },
  })
}

const summaryText = computed(() => {
  const s = previewResult.value?.summary
  if (!s) return ''
  return `改名 ${s.renamed} · 合并 ${s.merged} · 跳过 ${s.skipped}`
})
</script>

<template>
  <a-drawer v-model:visible="visible" title="批量高级编辑" :width="720" :footer="false">
    <div class="be-drawer">
      <!-- 规则列表 -->
      <div class="rules-area">
        <TagRuleForm
          v-for="(rule, i) in rules"
          :key="i"
          :rule="rule"
          :index="i"
          @change="(r: BatchEditRule) => onRuleChange(i, r)"
          @remove="removeRule(i)"
        />
        <a-button type="outline" long @click="addRule">+ 添加规则</a-button>
      </div>

      <!-- 预览结果 -->
      <div v-if="preview.length || previewing" class="preview-area">
        <div class="preview-head">
          <span>影响预览（{{ preview.length }} 条）</span>
          <span v-if="summaryText" class="summary-text">{{ summaryText }}</span>
        </div>
        <a-spin :loading="previewing">
          <a-table :data="preview" :pagination="false" size="small" :scroll="{ y: 240 }">
            <template #columns>
              <a-table-column title="原名称" data-index="from" />
              <a-table-column title="变化" :width="140">
                <template #cell="{ record }">
                  <template v-if="record.action === 'merge'">
                    → 合并到「{{ record.target?.name ?? record.to }}」
                  </template>
                  <template v-else-if="record.action === 'rename'">→ {{ record.to }}</template>
                  <template v-else>—</template>
                </template>
              </a-table-column>
              <a-table-column title="动作" :width="80">
                <template #cell="{ record }">{{ ACTION_LABELS[record.action] }}</template>
              </a-table-column>
              <a-table-column title="冲突" :width="80">
                <template #cell="{ record }">
                  <a-tag v-if="record.conflict" color="orange">目标已存在</a-tag>
                  <span v-else>—</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-spin>
      </div>

      <!-- 底部操作 -->
      <div class="be-footer">
        <a-space>
          <a-button type="primary" :loading="previewing" @click="runPreview">预览</a-button>
          <a-button type="primary" status="danger" :loading="executing" @click="confirmExecute">
            执行
          </a-button>
        </a-space>
        <span class="footer-hint">规则按顺序逐条作用于标签；新名已存在时自动合并到该标签</span>
      </div>
    </div>
  </a-drawer>
</template>

<style scoped>
.be-drawer {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.rules-area {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview-area {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
}
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}
.summary-text {
  font-weight: normal;
  color: #6b7280;
  font-size: 12px;
}
.be-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.footer-hint {
  font-size: 12px;
  color: #9ca3af;
}
</style>
