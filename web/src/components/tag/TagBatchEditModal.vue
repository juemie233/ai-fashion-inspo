<script setup lang="ts">
/** 统一批量编辑弹窗：整合三种批量操作模式。
 *
 * - 逐行编辑：每个选中标签一行，直接改名 / 改类别
 * - 查找替换：在选中标签名中批量查找替换（PATCH /tags/batch-rename）
 * - 批量改类别：把所有选中标签移到同一类别（PATCH /tags/batch-category）
 *
 * 高级正则编辑（TagBatchEditDrawer，dry-run 预览）仍作为独立入口保留。
 * 所有变更成功后广播标签变更事件，调用方无需手动回传刷新。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { batchChangeCategory, batchRenameTags, updateTag, type TagItem } from '@/api/tags'
import { CATEGORY_LABELS } from '@/constants/tag'
import { useTagEvents } from '@/composables/useTagEvents'

type Mode = 'inline' | 'replace' | 'category'

const visible = defineModel<boolean>('visible', { required: true })

const props = defineProps<{
  /** 待操作的标签（选中项快照） */
  tags: Array<Pick<TagItem, 'id' | 'name' | 'category'> & { description?: string | null }>
  /** 初始模式，默认逐行编辑 */
  initialMode?: Mode
}>()

const { notifyTagChanged } = useTagEvents()

const mode = ref<Mode>('inline')

watch(visible, (v) => {
  if (v) {
    mode.value = props.initialMode ?? 'inline'
    initInline()
    replaceFind.value = ''
    replaceTo.value = ''
    categoryTarget.value = ''
  }
})

const categoryOptions = computed(() =>
  Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
)

const count = computed(() => props.tags.length)

// ── 模式一：逐行编辑 ──
interface EditRow {
  id: number
  name: string
  category: string
  orig: { name: string; category: string }
}
const rows = ref<EditRow[]>([])

function initInline() {
  rows.value = props.tags.map((t) => ({
    id: t.id,
    name: t.name,
    category: t.category,
    orig: { name: t.name, category: t.category },
  }))
}

const inlineChanges = computed(() => {
  const out: Array<{ id: number; body: { name?: string; category?: string } }> = []
  for (const r of rows.value) {
    if (!r.name.trim()) continue
    const body: { name?: string; category?: string } = {}
    if (r.name.trim() !== r.orig.name) body.name = r.name.trim()
    if (r.category !== r.orig.category) body.category = r.category
    if (Object.keys(body).length) out.push({ id: r.id, body })
  }
  return out
})

// ── 模式二：查找替换 ──
const replaceFind = ref('')
const replaceTo = ref('')

// ── 模式三：批量改类别 ──
const categoryTarget = ref('')

const saving = ref(false)
const progress = ref('')

async function saveInline() {
  const changes = inlineChanges.value
  if (!changes.length) {
    Message.info('没有需要保存的修改')
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
  finishSave(
    ok,
    errors,
    changes.map((c) => c.id),
  )
}

async function saveReplace() {
  if (!replaceFind.value.trim() || !replaceTo.value.trim()) {
    Message.warning('请填写查找和替换内容')
    return
  }
  saving.value = true
  try {
    const ids = props.tags.map((t) => t.id)
    const data = await batchRenameTags(ids, replaceFind.value.trim(), replaceTo.value.trim())
    Message.success(`已更新 ${data.updated} 个标签`)
    notifyTagChanged({ type: 'batch-edited', tagIds: ids })
    visible.value = false
  } catch (e) {
    Message.error(getApiErrorMessage(e, '批量重命名失败'))
  } finally {
    saving.value = false
  }
}

async function saveCategory() {
  if (!categoryTarget.value) {
    Message.warning('请选择目标类别')
    return
  }
  saving.value = true
  try {
    const ids = props.tags.map((t) => t.id)
    const data = await batchChangeCategory(ids, categoryTarget.value)
    Message.success(`已将 ${data.updated} 个标签移至指定类别`)
    notifyTagChanged({ type: 'batch-edited', tagIds: ids })
    visible.value = false
  } catch (e) {
    Message.error(getApiErrorMessage(e, '批量改类别失败'))
  } finally {
    saving.value = false
  }
}

function finishSave(ok: number, errors: string[], changedIds: number[]) {
  if (!errors.length) {
    Message.success(`已保存 ${ok} 个标签的修改`)
    notifyTagChanged({ type: 'batch-edited', tagIds: changedIds })
    visible.value = false
  } else {
    Message.warning(`成功 ${ok} 个，失败 ${errors.length} 个：${errors.join('；')}`)
    if (ok > 0) notifyTagChanged({ type: 'batch-edited', tagIds: changedIds })
  }
}

function handleOk() {
  if (mode.value === 'inline') void saveInline()
  else if (mode.value === 'replace') void saveReplace()
  else void saveCategory()
}

const canSubmit = computed(() => {
  if (saving.value) return false
  if (mode.value === 'inline') return inlineChanges.value.length > 0
  if (mode.value === 'replace')
    return replaceFind.value.trim().length > 0 && replaceTo.value.trim().length > 0
  return categoryTarget.value.length > 0
})
</script>

<template>
  <a-modal
    v-model:visible="visible"
    title="批量编辑标签"
    :width="640"
    :ok-text="saving ? '保存中…' : '保存'"
    cancel-text="取消"
    :ok-loading="saving"
    :ok-button-props="{ disabled: !canSubmit }"
    @ok="handleOk"
    @cancel="visible = false"
  >
    <a-radio-group v-model="mode" type="button" size="small" style="margin-bottom: 12px">
      <a-radio value="inline">逐行编辑</a-radio>
      <a-radio value="replace">查找替换</a-radio>
      <a-radio value="category">批量改类别</a-radio>
    </a-radio-group>

    <div class="hint">共 {{ count }} 个标签</div>

    <!-- 逐行编辑 -->
    <div v-if="mode === 'inline'" class="form-body">
      <div class="form-head">
        <span class="col-name">标签名</span>
        <span class="col-cat">类别</span>
      </div>
      <div class="form-rows">
        <div v-for="row in rows" :key="row.id" class="form-row">
          <a-input v-model="row.name" :max-length="50" allow-clear />
          <a-select v-model="row.category" :options="categoryOptions" style="width: 120px" />
        </div>
      </div>
      <div v-if="progress" class="progress-hint">{{ progress }}</div>
    </div>

    <!-- 查找替换 -->
    <div v-else-if="mode === 'replace'" class="replace-body">
      <p style="color: #6b7280; font-size: 13px">在选中的 {{ count }} 个标签名中查找并替换文本：</p>
      <div class="field">
        <label class="field-label">查找</label>
        <a-input v-model="replaceFind" placeholder="如：白色" />
      </div>
      <div class="field">
        <label class="field-label">替换为</label>
        <a-input v-model="replaceTo" placeholder="如：纯白" />
      </div>
    </div>

    <!-- 批量改类别 -->
    <div v-else class="category-body">
      <p style="color: #6b7280; font-size: 13px">将选中的 {{ count }} 个标签移至：</p>
      <a-select
        v-model="categoryTarget"
        :options="categoryOptions"
        placeholder="选择目标类别"
        style="margin: 8px 0"
      />
    </div>
  </a-modal>
</template>

<style scoped>
.hint {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 8px;
}
.form-head {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
}
.form-head .col-name {
  flex: 1;
}
.form-head .col-cat {
  width: 120px;
}
.form-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
}
.form-row {
  display: flex;
  gap: 8px;
}
.progress-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #2a78d6;
}
.field {
  margin-bottom: 12px;
}
.field-label {
  display: block;
  font-size: 13px;
  color: #4b5563;
  margin-bottom: 4px;
}
</style>
