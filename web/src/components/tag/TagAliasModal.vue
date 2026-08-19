<script setup lang="ts">
/** 标签别名管理弹窗：查看/添加/删除某标签的别名。 */

import { getApiErrorMessage } from '@/utils/apiError'
import { ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { fetchAliases, createAlias, deleteAlias, type TagAlias, type TagItem } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{ tag: TagItem | null }>()

const aliasList = ref<TagAlias[]>([])
const newAlias = ref('')
const aliasLoading = ref(false)

watch(show, (v) => {
  if (v && props.tag) {
    newAlias.value = ''
    loadAliases()
  }
})

async function loadAliases() {
  aliasLoading.value = true
  try {
    const all = await fetchAliases()
    aliasList.value = all.filter((a) => a.tag_id === props.tag?.id)
  } catch { aliasList.value = [] } finally { aliasLoading.value = false }
}

async function handleAddAlias() {
  if (!props.tag || !newAlias.value.trim()) return
  try {
    await createAlias(props.tag.id, newAlias.value.trim())
    Message.success('别名已添加')
    newAlias.value = ''
    await loadAliases()
  } catch (e) {
    Message.error(getApiErrorMessage(e, '添加失败'))
  }
}

async function handleDeleteAlias(aliasId: number) {
  try {
    await deleteAlias(aliasId)
    Message.success('别名已删除')
    await loadAliases()
  } catch { Message.error('删除失败') }
}
</script>

<template>
  <a-modal v-model:visible="show" title="标签别名" :footer="false" :width="520">
    <p v-if="tag" style="font-size:13px;color:#999;margin-bottom:12px">
      「{{ tag.name }}」的别名：AI 识别到别名时会自动归为该标签
    </p>
    <a-space style="margin-bottom:12px">
      <a-input
        v-model="newAlias"
        placeholder="输入别名，如：纯白"
        style="width:240px"
        @press-enter="handleAddAlias"
      />
      <a-button type="primary" size="small" :disabled="!newAlias.trim()" @click="handleAddAlias">添加</a-button>
    </a-space>
    <a-spin :loading="aliasLoading">
      <a-list v-if="aliasList.length > 0" :bordered="true">
        <a-list-item v-for="a in aliasList" :key="a.id">
          <template #actions>
            <a-popconfirm :content="`确认删除别名「${a.alias}」？`" @ok="handleDeleteAlias(a.id)">
              <a-button size="mini" type="text" status="danger">删除</a-button>
            </a-popconfirm>
          </template>
          {{ a.alias }}
        </a-list-item>
      </a-list>
      <div v-else-if="!aliasLoading" style="text-align:center;color:#999;padding:20px">暂无别名</div>
    </a-spin>
  </a-modal>
</template>
