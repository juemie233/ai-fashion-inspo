<script setup lang="ts">
/** 人物头像设置弹窗：从 TA 的素材里选一张，或本地上传一张照片（两者二选一）。
 *
 * 为什么单独一个组件：详情页头部与编辑弹窗都要能设置头像，且都要「素材网格 + 上传」
 * 两套来源；收敛在这里避免两处各写一遍（以及两处各自踩 FormData/分页的坑）。
 *
 * 素材网格复用 common 公共组件 DensityImageGrid（列数随密度、单元 min-width: 0），
 * 与素材库各处口径一致；上传走 multipart（后端统一转 JPEG 且长边压到 512）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { bloggersApi, type PersonInspiration } from '@/api/persons'
import { getApiErrorMessage } from '@/utils/apiError'
import { getFileUrl } from '@/api/inspirations'
import DensityImageGrid from '@/components/common/DensityImageGrid.vue'

const props = defineProps<{
  visible: boolean
  personId: number
  personName: string
  /** 该人物是否已设置手动头像（决定是否显示「清除头像」） */
  hasAvatar?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  /** 头像已更新/清除，父组件据此刷新详情与列表 */
  (e: 'saved', avatarPath: string | null): void
}>()

/** 每页素材数（与人物详情的素材分页同量级） */
const PAGE_SIZE = 24

const tab = ref<'material' | 'upload'>('material')
const items = ref<PersonInspiration[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const submitting = ref(false)
const selectedInspId = ref<string>('')
/** 本地上传待提交的文件（选完文件后需点「设为头像」确认） */
const pickedFile = ref<File | null>(null)
const pickedPreview = ref('')

const canSubmit = computed(() =>
  tab.value === 'material' ? Boolean(selectedInspId.value) : Boolean(pickedFile.value),
)

async function loadMaterials(targetPage = 1) {
  loading.value = true
  try {
    const data = await bloggersApi.fetchInspirations(props.personId, targetPage, PAGE_SIZE)
    items.value = data.items
    total.value = data.total
    page.value = targetPage
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载 TA 的素材失败'))
  } finally {
    loading.value = false
  }
}

// 打开弹窗时重置状态并加载第一页素材（人物可能带很多素材，按需翻页）。
// immediate：父组件若以「v-if + visible=true」方式挂载，这里同样要加载
watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    tab.value = 'material'
    selectedInspId.value = ''
    clearPickedFile()
    if (props.personId) void loadMaterials(1)
  },
  { immediate: true },
)

function clearPickedFile() {
  if (pickedPreview.value) URL.revokeObjectURL(pickedPreview.value)
  pickedFile.value = null
  pickedPreview.value = ''
}

function onFileChange(fileList: FileList | undefined) {
  const file = fileList?.[0]
  clearPickedFile()
  if (!file) return
  pickedFile.value = file
  pickedPreview.value = URL.createObjectURL(file)
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const blogger = await bloggersApi.setAvatar(props.personId, {
      inspirationId: tab.value === 'material' ? selectedInspId.value : undefined,
      file: tab.value === 'upload' ? (pickedFile.value ?? undefined) : undefined,
    })
    Message.success(`已设置「${props.personName}」的头像`)
    emit('saved', blogger.avatar_path ?? null)
    emit('update:visible', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '设置头像失败'))
  } finally {
    submitting.value = false
  }
}

async function clearAvatar() {
  submitting.value = true
  try {
    await bloggersApi.clearAvatar(props.personId)
    Message.success('已清除手动头像，改用自动匹配的人脸小图')
    emit('saved', null)
    emit('update:visible', false)
  } catch (e) {
    Message.error(getApiErrorMessage(e, '清除头像失败'))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="`设置「${personName}」的头像`"
    :width="720"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <a-tabs v-model:active-key="tab" size="small" type="line">
      <!-- 主路径：从 TA 自己的素材里挑一张（头像应来自这个人的照片） -->
      <a-tab-pane key="material" title="从 TA 的素材选择">
        <a-spin :loading="loading" style="display: block">
          <DensityImageGrid :show-switch="false" density="compact">
            <div
              v-for="item in items"
              :key="item.inspiration_id"
              class="avatar-pick-cell"
              :class="{ checked: selectedInspId === item.inspiration_id }"
              :title="item.media_type === 'video' ? '视频素材：用首帧画面' : ''"
              @click="selectedInspId = item.inspiration_id"
            >
              <img :src="getFileUrl(item.thumbnail_path || item.file_path)" alt="素材" />
              <a-tag v-if="item.media_type === 'video'" class="avatar-pick-badge" size="small">
                视频首帧
              </a-tag>
              <div v-if="selectedInspId === item.inspiration_id" class="avatar-pick-check">✓</div>
            </div>
          </DensityImageGrid>
          <a-empty
            v-if="!loading && items.length === 0"
            description="TA 还没有素材，可切到「上传照片」"
            style="padding: 24px 0"
          />
          <a-pagination
            v-if="total > PAGE_SIZE"
            style="margin-top: 12px; justify-content: center"
            :current="page"
            :page-size="PAGE_SIZE"
            :total="total"
            @change="(p: number) => loadMaterials(p)"
          />
        </a-spin>
      </a-tab-pane>

      <!-- 兜底：本地上传一张照片（TA 的素材里没有合适的一张时） -->
      <a-tab-pane key="upload" title="上传照片">
        <div class="avatar-upload-row">
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            @change="(e: Event) => onFileChange((e.target as HTMLInputElement).files ?? undefined)"
          />
        </div>
        <div class="avatar-upload-tip">
          支持 JPG / PNG / WebP；上传后统一转成方形头像（长边 ≤512）， 展示优先级为「手动头像 →
          人脸小图 → 首字」。
        </div>
        <div v-if="pickedPreview" class="avatar-upload-preview">
          <img :src="pickedPreview" alt="待设置的头像" />
        </div>
      </a-tab-pane>
    </a-tabs>

    <template #footer>
      <a-space style="display: flex; justify-content: space-between; width: 100%">
        <a-popconfirm
          v-if="hasAvatar"
          content="清除手动头像？将回退为自动匹配的人脸小图（或首字占位）。"
          @ok="clearAvatar"
        >
          <a-button type="text" status="danger" :loading="submitting">清除头像</a-button>
        </a-popconfirm>
        <span v-else />
        <a-space>
          <a-button @click="emit('update:visible', false)">取消</a-button>
          <a-button type="primary" :loading="submitting" :disabled="!canSubmit" @click="submit">
            设为头像
          </a-button>
        </a-space>
      </a-space>
    </template>
  </a-modal>
</template>

<style scoped>
.avatar-pick-cell {
  position: relative;
  aspect-ratio: 1;
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  border: 2px solid transparent;
  background: #f2f3f5;
}

.avatar-pick-cell img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.avatar-pick-cell.checked {
  border-color: #165dff;
}

.avatar-pick-check {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #165dff;
  color: #fff;
  font-size: 12px;
  line-height: 18px;
  text-align: center;
}

.avatar-pick-badge {
  position: absolute;
  left: 2px;
  bottom: 2px;
}

.avatar-upload-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.avatar-upload-tip {
  margin-top: 8px;
  font-size: 12px;
  color: #86909c;
  line-height: 1.7;
}

.avatar-upload-preview {
  margin-top: 12px;
}

.avatar-upload-preview img {
  width: 96px;
  height: 96px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid #e5e6eb;
}
</style>
