<script setup lang="ts">
/** 标签别名管理弹窗：查看/添加/删除某标签的别名。 */

import { ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { fetchAliases, createAlias, deleteAlias, type TagAlias, type TagItem } from '@/api/tags'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{ tag: TagItem | null }>()

const message = useMessage()

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
    message.success('别名已添加')
    newAlias.value = ''
    await loadAliases()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '添加失败')
  }
}

async function handleDeleteAlias(aliasId: number) {
  try {
    await deleteAlias(aliasId)
    message.success('别名已删除')
    await loadAliases()
  } catch { message.error('删除失败') }
}
</script>

<template>
  <n-modal v-model:show="show" title="标签别名" preset="card" style="width:520px">
    <p v-if="tag" style="font-size:13px;color:#999;margin-bottom:12px">
      「{{ tag.name }}」的别名：AI 识别到别名时会自动归为该标签
    </p>
    <n-space align="center" style="margin-bottom:12px">
      <n-input
        v-model:value="newAlias"
        placeholder="输入别名，如：纯白"
        style="width:240px"
        @keyup.enter="handleAddAlias"
      />
      <n-button type="primary" size="small" :disabled="!newAlias.trim()" @click="handleAddAlias">添加</n-button>
    </n-space>
    <n-spin :show="aliasLoading">
      <n-list v-if="aliasList.length > 0" bordered>
        <n-list-item v-for="a in aliasList" :key="a.id">
          <template #suffix>
            <n-popconfirm @positive-click="handleDeleteAlias(a.id)">
              <template #trigger><n-button size="tiny" text type="error">删除</n-button></template>
              确认删除别名「{{ a.alias }}」？
            </n-popconfirm>
          </template>
          {{ a.alias }}
        </n-list-item>
      </n-list>
      <div v-else-if="!aliasLoading" style="text-align:center;color:#999;padding:20px">暂无别名</div>
    </n-spin>
  </n-modal>
</template>
