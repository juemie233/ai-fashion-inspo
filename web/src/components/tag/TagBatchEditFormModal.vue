<script setup lang="ts">
/** 批量编辑标签弹窗：逐行直接编辑标签名 / 类别 / 备注。
 *
 * 与「批量高级编辑」抽屉（正则规则 + dry-run）不同，本组件面向健康度面板
 * 勾选少量标签后做直接修改：每行一个表单，保存时仅提交发生变化的字段，
 * 逐条调用 PATCH /tags/{id}，汇总成功/失败结果。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { CATEGORY_LABELS, updateTag, type TagItem } from '@/api/tags'

const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ saved: [] }>()

const props = defineProps<{
  /** 待编辑的标签列表（健康度面板的勾选行 / 任意标签列表）；description 可缺省 */
  tags: Array<Pick<TagItem, 'id' | 'name' | 'category'> & { description?: string | null }>
}>()

/** 单行编辑副本 */
interface EditRow {
  id: number
  name: string
  category: string
  description: string
  /** 原始值快照，用于判断本行是否有改动 */
  _orig: { name: string; category: string; description: string }
}

const rows = ref<EditRow[]>([])
const saving = ref(false)
/** 保存过程中的进度文本（成功 N / 总数 M） */
const progress = ref('')

watch(visible, (v) => {
  if (v) {
    rows.value = props.tags.map((t) => ({
      id: t.id,
      name: t.name,
      category: t.category,
      description: t.description ?? '',
      _orig: {
        name: t.name,
        category: t.category,
        description: t.description ?? '',
      },
    }))
    progress.value = ''
  }
})

/** 类别下拉选项 */
const categoryOptions = computed(() =>
  Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
)

/** 是否存在至少一行有改动（空名称不计入，会被拦截） */
const hasChange = computed(() =>
  rows.value.some((r) => {
    if (!r.name.trim()) return false
    return (
      r.name.trim() !== r._orig.name ||
      r.category !== r._orig.category ||
      (r.description.trim() || '') !== (r._orig.description || '')
    )
  }),
)

/** 收集所有变更行的更新载荷 */
function collectChanges(): Array<{
  id: number
  body: { name?: string; category?: string; description?: string | null }
}> {
  const out: Array<{
    id: number
    body: { name?: string; category?: string; description?: string | null }
  }> = []
  for (const r of rows.value) {
    if (!r.name.trim()) continue
    const body: { name?: string; category?: string; description?: string | null } = {}
    if (r.name.trim() !== r._orig.name) body.name = r.name.trim()
    if (r.category !== r._orig.category) body.category = r.category
    const newDesc = r.description.trim() || null
    if (newDesc !== (r._orig.description || null)) body.description = newDesc
    if (Object.keys(body).length > 0) out.push({ id: r.id, body })
  }
  return out
}

async function handleSave() {
  const changes = collectChanges()
  if (!changes.length) {
    Message.info('没有需要保存的修改')
    return
  }
  // 名称空值校验
  const empty = rows.value.find((r) => !r.name.trim())
  if (empty) {
    Message.warning('标签名不能为空')
    return
  }

  saving.value = true
  let ok = 0
  const errors: string[] = []
  for (let i = 0; i < changes.length; i++) {
    const { id, body } = changes[i]
    progress.value = `正在保存 ${i + 1} / ${changes.length}…`
    try {
      await updateTag(id, body)
      ok += 1
    } catch (e) {
      errors.push(getApiErrorMessage(e, `标签 #${id} 更新失败`))
    }
  }
  saving.value = false
  progress.value = ''

  if (errors.length === 0) {
    Message.success(`已保存 ${ok} 个标签的修改`)
    visible.value = false
    emit('saved')
  } else {
    Message.warning(`成功 ${ok} 个，失败 ${errors.length} 个：${errors.join('；')}`)
    if (ok > 0) emit('saved')
  }
}
</script>

<template>
  <a-modal
    v-model:visible="visible"
    title="批量编辑标签"
    :width="640"
    :ok-text="saving ? '保存中…' : '保存'"
    cancel-text="取消"
    :ok-loading="saving"
    :ok-button-props="{ disabled: !hasChange }"
    @ok="handleSave"
    @cancel="visible = false"
  >
    <div v-if="rows.length === 0" class="empty-hint">未选择任何标签</div>
    <div v-else class="batch-form">
      <div class="form-head">
        <span class="col-name">标签名</span>
        <span class="col-cat">类别</span>
      </div>
      <div class="form-body">
        <div v-for="row in rows" :key="row.id" class="form-row">
          <a-input
            v-model="row.name"
            placeholder="标签名"
            :max-length="50"
            allow-clear
          />
          <a-select
            v-model="row.category"
            :options="categoryOptions"
            :style="{ width: '120px' }"
          />
        </div>
      </div>
      <div v-if="progress" class="progress-hint">{{ progress }}</div>
      <div class="form-foot">共 {{ rows.length }} 个标签，仅保存发生变化的行</div>
    </div>
  </a-modal>
</template>

<style scoped>
.empty-hint {
  padding: 24px 0;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
}
.batch-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.form-head {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #6b7280;
  padding: 0 2px;
}
.form-head .col-name {
  flex: 1;
}
.form-head .col-cat {
  width: 120px;
}
.form-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 480px;
  overflow-y: auto;
  padding-right: 2px;
}
.form-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.form-foot {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
}
.progress-hint {
  font-size: 12px;
  color: #2a78d6;
}
</style>
