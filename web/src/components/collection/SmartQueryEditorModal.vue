<script setup lang="ts">
/** 智能合集条件编辑弹窗：创建/更新智能合集的筛选条件。
 *
 * 字段与 SmartCollectionQuery 契约一一对应（关键词/标签 AND-OR/来源/媒体/
 * 收藏/评分/日期）；条件与素材库同口径，动态求值逻辑在后端。
 */

import { computed, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useTagsStore } from '@/stores/tags'
import { createCollection, updateCollection, type SmartCollectionQuery } from '@/api/collections'
import { getApiErrorMessage } from '@/utils/apiError'

const props = defineProps<{
  visible: boolean
  /** null = 创建新智能合集；否则为编辑既有智能合集（传 id 与原始条件） */
  collectionId: number | null
  initialQuery: SmartCollectionQuery | null
  initialName: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  /** 创建/更新成功后触发 */
  (e: 'saved'): void
}>()

const tagsStore = useTagsStore()
const submitting = ref(false)

const form = reactive({
  name: '',
  keyword: '',
  tagIds: [] as number[],
  tagMode: 'and' as 'and' | 'or',
  sourceTypes: [] as string[],
  mediaType: '' as '' | 'image' | 'video',
  onlyFavorite: false,
  minRating: 0,
  dateRange: [] as string[],
})

const tagOptions = computed(() =>
  tagsStore.groups.flatMap((g) => g.tags.map((t) => ({ label: t.name, value: t.id }))),
)
const sourceOptions = [
  { label: '手动上传', value: 'manual_upload' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '抖音', value: 'douyin' },
  { label: '浏览器插件', value: 'browser_extension' },
]
const ratingOptions = [
  { label: '不限', value: 0 },
  { label: '1 星及以上', value: 1 },
  { label: '2 星及以上', value: 2 },
  { label: '3 星及以上', value: 3 },
  { label: '4 星及以上', value: 4 },
  { label: '5 星', value: 5 },
]

watch(
  () => props.visible,
  (v) => {
    if (!v) return
    void tagsStore.load()
    const q = props.initialQuery
    form.name = props.initialName
    form.keyword = q?.keyword ?? ''
    form.tagIds = q?.tag_ids ? [...q.tag_ids] : []
    form.tagMode = q?.tag_mode ?? 'and'
    form.sourceTypes = q?.source_types ? [...q.source_types] : []
    form.mediaType = q?.media_type ?? ''
    form.onlyFavorite = q?.is_favorite === true
    form.minRating = q?.min_rating ?? 0
    form.dateRange = q?.start_date && q?.end_date ? [q.start_date, q.end_date] : []
  },
)

function buildQuery(): SmartCollectionQuery {
  const query: SmartCollectionQuery = {}
  if (form.keyword.trim()) query.keyword = form.keyword.trim()
  if (form.tagIds.length > 0) {
    query.tag_ids = form.tagIds
    query.tag_mode = form.tagMode
  }
  if (form.sourceTypes.length > 0) query.source_types = form.sourceTypes
  if (form.mediaType) query.media_type = form.mediaType
  if (form.onlyFavorite) query.is_favorite = true
  if (form.minRating > 0) query.min_rating = form.minRating
  if (form.dateRange.length === 2) {
    query.start_date = form.dateRange[0]
    query.end_date = form.dateRange[1]
  }
  return query
}

async function handleSave() {
  const name = form.name.trim()
  if (!name) {
    Message.warning('请输入合集名称')
    return
  }
  submitting.value = true
  try {
    const query = buildQuery()
    if (props.collectionId === null) {
      await createCollection({ name, query_json: query })
      Message.success(`已创建智能合集「${name}」`)
    } else {
      await updateCollection(props.collectionId, { name, query_json: query })
      Message.success('智能合集条件已更新')
    }
    emit('saved')
    emit('update:visible', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '保存智能合集失败'))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="collectionId === null ? '新建智能合集' : '编辑智能合集条件'"
    :width="520"
    @update:visible="emit('update:visible', $event)"
  >
    <p style="color: #999; font-size: 12px; margin-top: 0">
      智能合集按条件动态匹配素材库（不含垃圾桶素材），素材库变化时内容自动更新。
    </p>
    <a-form :model="form" label-align="left" :label-col-style="{ width: '84px' }" size="small">
      <a-form-item label="合集名称">
        <a-input v-model="form.name" placeholder="1~50 字" allow-clear max-length="50" />
      </a-form-item>
      <a-form-item label="关键词">
        <a-input v-model="form.keyword" placeholder="可空；匹配文件名/来源等关键字段" allow-clear />
      </a-form-item>
      <a-form-item label="标签">
        <div style="display: flex; gap: 8px; align-items: center">
          <a-select
            v-model="form.tagIds"
            multiple
            filterable
            allow-clear
            :options="tagOptions"
            placeholder="可多选"
            style="flex: 1"
          />
          <a-radio-group
            v-model="form.tagMode"
            size="mini"
            type="button"
            :disabled="form.tagIds.length === 0"
          >
            <a-radio value="and">同时包含</a-radio>
            <a-radio value="or">任一包含</a-radio>
          </a-radio-group>
        </div>
      </a-form-item>
      <a-form-item label="来源">
        <a-select
          v-model="form.sourceTypes"
          multiple
          allow-clear
          :options="sourceOptions"
          placeholder="可空 = 全部来源"
        />
      </a-form-item>
      <a-form-item label="媒体类型">
        <a-radio-group v-model="form.mediaType" size="mini" type="button">
          <a-radio value="">全部</a-radio>
          <a-radio value="image">图片</a-radio>
          <a-radio value="video">视频</a-radio>
        </a-radio-group>
      </a-form-item>
      <a-form-item label="收藏">
        <a-checkbox v-model="form.onlyFavorite">仅收藏素材</a-checkbox>
      </a-form-item>
      <a-form-item label="评分">
        <a-select v-model="form.minRating" :options="ratingOptions" style="width: 150px" />
      </a-form-item>
      <a-form-item label="入库日期">
        <a-range-picker v-model="form.dateRange" style="width: 100%" />
      </a-form-item>
    </a-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <a-button size="small" @click="emit('update:visible', false)">取消</a-button>
        <a-button size="small" type="primary" :loading="submitting" @click="handleSave">
          保存
        </a-button>
      </div>
    </template>
  </a-modal>
</template>
