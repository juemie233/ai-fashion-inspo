<script setup lang="ts">
/** 编辑标签弹窗：重命名 / 改类别 / 改备注。 */

import { ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { updateTag, CATEGORY_LABELS, type TagItem } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ saved: [] }>()

const props = defineProps<{ tag: TagItem | null }>()

const message = useMessage()

const editName = ref('')
const editCategory = ref('')
const editDescription = ref('')

watch(show, (v) => {
  if (v && props.tag) {
    editName.value = props.tag.name
    editCategory.value = props.tag.category
    editDescription.value = props.tag.description || ''
  }
})

async function handleEdit() {
  if (!props.tag || !editName.value.trim()) return
  try {
    await updateTag(props.tag.id, {
      name: editName.value.trim() !== props.tag.name ? editName.value.trim() : undefined,
      category: editCategory.value !== props.tag.category ? editCategory.value : undefined,
      description: (editDescription.value.trim() || null) !== (props.tag.description || null)
        ? editDescription.value.trim() || null
        : undefined,
    })
    message.success('标签已更新')
    show.value = false
    emit('saved')
  } catch (e: any) {
    message.error(e.response?.data?.detail || '更新失败')
  }
}
</script>

<template>
  <n-modal v-model:show="show" title="编辑标签" preset="card" style="width:420px" @esc="show = false">
    <n-form label-placement="left" label-width="60">
      <n-form-item label="名称">
        <n-input v-model:value="editName" @keyup.enter="handleEdit" />
      </n-form-item>
      <n-form-item label="类别">
        <n-select
          v-model:value="editCategory"
          :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
        />
      </n-form-item>
      <n-form-item label="备注">
        <n-input
          v-model:value="editDescription"
          type="textarea"
          :rows="2"
          maxlength="255"
          show-count
          placeholder="标签说明（可选）"
        />
      </n-form-item>
    </n-form>
    <n-space justify="end" style="margin-top:16px">
      <n-button @click="show = false">取消</n-button>
      <n-button type="primary" @click="handleEdit">保存</n-button>
    </n-space>
  </n-modal>
</template>
