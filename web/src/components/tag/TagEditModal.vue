<script setup lang="ts">
/** 编辑标签弹窗：重命名 / 改类别 / 改备注。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { updateTag, CATEGORY_LABELS, type TagItem } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ saved: [] }>()

const props = defineProps<{ tag: TagItem | null }>()

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
    Message.success('标签已更新')
    show.value = false
    emit('saved')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '更新失败'))
  }
}
</script>

<template>
  <a-modal v-model:visible="show" title="编辑标签" :footer="false" :width="420" @cancel="show = false">
    <a-form :model="{ editName, editCategory, editDescription }" label-align="left" :label-col-style="{ width: '60px' }">
      <a-form-item label="名称">
        <a-input v-model="editName" @press-enter="handleEdit" />
      </a-form-item>
      <a-form-item label="类别">
        <a-select
          v-model="editCategory"
          :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
        />
      </a-form-item>
      <a-form-item label="备注">
        <a-textarea
          v-model="editDescription"
          :auto-size="{ minRows: 2 }"
          :max-length="255"
          show-word-limit
          placeholder="标签说明（可选）"
        />
      </a-form-item>
    </a-form>
    <a-space style="display:flex;justify-content:flex-end;margin-top:16px">
      <a-button @click="show = false">取消</a-button>
      <a-button type="primary" @click="handleEdit">保存</a-button>
    </a-space>
  </a-modal>
</template>
