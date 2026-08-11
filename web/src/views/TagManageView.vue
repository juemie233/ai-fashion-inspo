<script setup lang="ts">
/** 标签管理页：浏览/编辑/合并标签，管理标签体系。 */

import { ref, onMounted, h } from 'vue'
import { useMessage } from 'naive-ui'
import apiClient from '@/api/client'
import {
  fetchTagsGrouped,
  createTag,
  mergeTags,
  type TagCategoryGroup,
  CATEGORY_LABELS,
} from '@/api/tags'

const message = useMessage()

const groups = ref<TagCategoryGroup[]>([])
const loading = ref(false)

/** 新建标签表单 */
const showCreateForm = ref(false)
const newTagName = ref('')
const newTagCategory = ref('free')

/** 合并标签对话框 */
const showMergeDialog = ref(false)
const mergeSource = ref<{ id: number; name: string } | null>(null)
const mergeTarget = ref<number | null>(null)

onMounted(async () => {
  loading.value = true
  try {
    groups.value = await fetchTagsGrouped()
  } catch {
    message.error('加载标签失败')
  } finally {
    loading.value = false
  }
})

async function handleCreate() {
  if (!newTagName.value.trim()) return
  try {
    await createTag(newTagName.value.trim(), newTagCategory.value)
    message.success('标签已创建')
    showCreateForm.value = false
    newTagName.value = ''
    // 刷新列表
    groups.value = await fetchTagsGrouped()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  }
}

async function handleDelete(tagId: number, tagName: string) {
  try {
    await apiClient.delete(`/tags/${tagId}`)
    message.success(`已删除标签 "${tagName}"`)
    groups.value = await fetchTagsGrouped()
  } catch {
    message.error('删除失败')
  }
}

async function handleMerge() {
  if (!mergeSource.value || !mergeTarget.value) return
  try {
    await mergeTags(mergeSource.value.id, mergeTarget.value)
    message.success(`已合并标签`)
    showMergeDialog.value = false
    mergeSource.value = null
    mergeTarget.value = null
    groups.value = await fetchTagsGrouped()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '合并失败')
  }
}

/** 可合并的目标标签（排除自己） */
function mergeTargetOptions() {
  if (!mergeSource.value) return []
  const opts: Array<{ label: string; value: number }> = []
  for (const group of groups.value) {
    for (const tag of group.tags) {
      if (tag.id !== mergeSource.value.id) {
        opts.push({ label: `${tag.name} (${CATEGORY_LABELS[tag.category] || tag.category})`, value: tag.id })
      }
    }
  }
  return opts
}
</script>

<template>
  <div class="tag-page">
    <div class="page-header">
      <h2>标签管理</h2>
      <n-button type="primary" @click="showCreateForm = !showCreateForm">
        {{ showCreateForm ? '取消' : '新标签' }}
      </n-button>
    </div>

    <!-- 新建标签表单 -->
    <n-card v-if="showCreateForm" title="创建新标签" style="margin-bottom: 24px">
      <n-space align="flex-end">
        <n-form-item label="标签名">
          <n-input v-model:value="newTagName" placeholder="例如: 森系" />
        </n-form-item>
        <n-form-item label="类别">
          <n-select
            v-model:value="newTagCategory"
            :options="Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ label: v, value: k }))"
            style="width: 160px"
          />
        </n-form-item>
        <n-button type="primary" @click="handleCreate">创建</n-button>
      </n-space>
    </n-card>

    <!-- 标签分组列表 -->
    <n-spin :show="loading">
      <n-collapse>
        <n-collapse-item
          v-for="group in groups"
          :key="group.category"
          :title="`${CATEGORY_LABELS[group.category] || group.category} (${group.tags.length})`"
        >
          <n-list hoverable clickable>
            <n-list-item v-for="tag in group.tags" :key="tag.id">
              <template #prefix>
                <n-tag size="small" :bordered="false">
                  {{ tag.usage_count }} 次
                </n-tag>
              </template>
              <span>{{ tag.name }}</span>
              <template #suffix>
                <n-space>
                  <n-button
                    size="tiny"
                    text
                    type="info"
                    @click="mergeSource = { id: tag.id, name: tag.name }; showMergeDialog = true"
                  >
                    合并
                  </n-button>
                  <n-popconfirm @positive-click="handleDelete(tag.id, tag.name)">
                    <template #trigger>
                      <n-button size="tiny" text type="error">删除</n-button>
                    </template>
                    确定删除标签 "{{ tag.name }}"？
                  </n-popconfirm>
                </n-space>
              </template>
            </n-list-item>
          </n-list>
        </n-collapse-item>
      </n-collapse>

      <n-empty
        v-if="!loading && groups.length === 0"
        description="暂无标签"
        size="small"
      />
    </n-spin>

    <!-- 合并对话框 -->
    <n-modal v-model:show="showMergeDialog" title="合并标签" preset="card" style="width: 500px">
      <p v-if="mergeSource">
        将 <strong>{{ mergeSource.name }}</strong> 合并到：
      </p>
      <n-select
        v-model:value="mergeTarget"
        :options="mergeTargetOptions()"
        placeholder="选择目标标签"
        filterable
        style="margin: 16px 0"
      />
      <n-space justify="end">
        <n-button @click="showMergeDialog = false">取消</n-button>
        <n-button type="primary" @click="handleMerge" :disabled="!mergeTarget">
          确认合并
        </n-button>
      </n-space>
    </n-modal>
  </div>
</template>

<style scoped>
.tag-page {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
}
</style>
