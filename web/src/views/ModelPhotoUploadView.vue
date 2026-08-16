<script setup lang="ts">
/** 添加模特照片页：只能选择文件夹，把一个文件夹整组导入为某个人物的「照片组」。
 *
 * 与「上传穿搭素材」（UploadView）分离：模特写真不进入素材库，不参与 AI 打标，
 * 仅按「人物 → 照片组 → 照片」浏览（见人物详情页）。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { fetchPersons, createPersonPhotoSet, uploadPersonPhoto } from '@/api/persons'
import type { Person } from '@shared/types/person'
import PersonFormModal from '@/components/person/PersonFormModal.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

// ── 人物选择 ──
const persons = ref<Person[]>([])
const personsLoading = ref(false)
const personId = ref<number | null>(null)
const showForm = ref(false)

async function loadPersons() {
  personsLoading.value = true
  try {
    const data = await fetchPersons({ page: 1, size: 200, sort: 'name' })
    persons.value = data.items
  } catch {
    message.error('加载人物列表失败')
  } finally {
    personsLoading.value = false
  }
}

/** 新建人物成功后：刷新列表并自动选中新人物 */
async function onPersonCreated(p: Person) {
  await loadPersons()
  personId.value = p.id
}

const personOptions = computed(() =>
  persons.value.map((p) => ({ label: p.name, value: p.id })),
)

// ── 文件夹选择（仅文件夹，禁用多文件选择）──
const folderInput = ref<HTMLInputElement | null>(null)
const setName = ref('')

interface PendingPhoto {
  id: string
  file: File
  thumbnail: string
  status: 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'
  progress: number
  errorMsg?: string
}

const pending = ref<PendingPhoto[]>([])

/** 可上传的图片扩展名（模特写真仅图片，不含视频） */
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif'])

function openFolder() {
  folderInput.value?.click()
}

function onFolderChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  // 文件夹名取 webkitRelativePath 的第一段（所选文件夹本身）
  const firstRelative = files[0]?.webkitRelativePath || ''
  const folderName = firstRelative.split('/')[0] || ''

  const imageFiles = files.filter((f) => {
    const ext = '.' + (f.name.split('.').pop()?.toLowerCase() || '')
    return IMAGE_EXTS.has(ext)
  })
  if (imageFiles.length === 0) {
    message.warning('该文件夹中没有可识别的图片文件')
    input.value = ''
    return
  }

  // 按文件名自然排序（模特写真常以 001/002 命名），保持组内顺序稳定
  const sorted = imageFiles.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))

  clearPending()
  for (const file of sorted) {
    pending.value.push({
      id: crypto.randomUUID(),
      file,
      thumbnail: URL.createObjectURL(file),
      status: 'pending',
      progress: 0,
    })
  }

  if (folderName && !setName.value.trim()) {
    setName.value = folderName
  }
  input.value = ''
}

function clearPending() {
  pending.value.forEach((p) => URL.revokeObjectURL(p.thumbnail))
  pending.value = []
}

// ── 上传 ──
const uploading = ref(false)
const uploadedSetId = ref<number | null>(null)

const stats = computed(() => {
  const done = pending.value.filter((p) => p.status === 'done').length
  const failed = pending.value.filter((p) => p.status === 'failed').length
  const dups = pending.value.filter((p) => p.status === 'duplicate').length
  return { done, failed, dups, total: pending.value.length }
})

async function startUpload() {
  if (!personId.value) {
    message.warning('请先选择人物')
    return
  }
  if (pending.value.length === 0) {
    message.warning('请先选择一个文件夹')
    return
  }
  uploading.value = true
  uploadedSetId.value = null
  try {
    const set = await createPersonPhotoSet(personId.value, setName.value.trim() || undefined)
    uploadedSetId.value = set.id

    for (let i = 0; i < pending.value.length; i++) {
      const item = pending.value[i]
      item.status = 'uploading'
      item.progress = 0
      try {
        await uploadPersonPhoto(personId.value, set.id, item.file, i, (e: any) => {
          if (e?.total > 0) {
            item.progress = Math.min(100, Math.round((e.loaded / e.total) * 100))
          }
        })
        item.status = 'done'
        item.progress = 100
      } catch (err: any) {
        if (err?.response?.status === 409) {
          item.status = 'duplicate'
          item.errorMsg = '内容重复已跳过'
        } else {
          item.status = 'failed'
          item.errorMsg = err?.response?.data?.detail || '上传失败'
        }
      }
    }

    const { done, failed, dups } = stats.value
    const parts = [`${done} 成功`]
    if (failed > 0) parts.push(`${failed} 失败`)
    if (dups > 0) parts.push(`${dups} 重复跳过`)
    message.success(`照片组「${set.name}」导入完成：${parts.join('，')}`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '创建照片组失败')
  } finally {
    uploading.value = false
  }
}

/** 跳转到人物详情查看刚导入的照片组 */
function goPersonDetail() {
  if (personId.value) router.push(`/persons/${personId.value}`)
}

// ── 生命周期 ──
onMounted(() => {
  loadPersons()
  // 从人物详情「添加照片」入口带入 person_id 时预选
  const q = Number(route.query.person_id)
  if (Number.isInteger(q) && q > 0) personId.value = q
})

onUnmounted(() => {
  clearPending()
})
</script>

<template>
  <div class="model-photo-page">
    <h2>添加模特照片</h2>
    <n-text depth="3" style="font-size: 13px">
      选择一个文件夹，把其中所有图片作为一组模特写真导入到某个人物名下
    </n-text>

    <!-- 人物选择 -->
    <n-card size="small" class="step-card" title="第一步 · 选择人物">
      <n-space align="center">
        <n-select
          v-model:value="personId"
          :options="personOptions"
          :loading="personsLoading"
          filterable
          clearable
          placeholder="选择模特 / 博主"
          style="width: 280px"
        />
        <n-button secondary @click="showForm = true">＋ 新建人物</n-button>
      </n-space>
    </n-card>

    <!-- 文件夹选择 -->
    <n-card size="small" class="step-card" title="第二步 · 选择文件夹">
      <div class="folder-zone" @click="openFolder">
        <div class="folder-icon">📁</div>
        <p class="folder-title">点击选择文件夹</p>
        <p class="folder-desc">仅支持选择文件夹，会把文件夹内全部图片导入为一组</p>
      </div>
      <input
        ref="folderInput"
        type="file"
        webkitdirectory
        multiple
        accept="image/*"
        style="display: none"
        @change="onFolderChange"
      />

      <n-form-item label="照片组名称" style="margin-top: 16px; max-width: 420px">
        <n-input v-model:value="setName" placeholder="默认取文件夹名，可修改" maxlength="128" />
      </n-form-item>
    </n-card>

    <!-- 预览 -->
    <n-card v-if="pending.length > 0" size="small" class="step-card">
      <template #header>
        <n-space align="center" justify="space-between">
          <span>已选 {{ pending.length }} 张照片</span>
          <n-space>
            <n-button size="small" quaternary :disabled="uploading" @click="clearPending">
              清空
            </n-button>
          </n-space>
        </n-space>
      </template>

      <div class="preview-grid">
        <div v-for="(p, i) in pending" :key="p.id" class="preview-item" :class="p.status">
          <img :src="p.thumbnail" :alt="p.file.name" />
          <div class="preview-index">{{ i + 1 }}</div>
          <div v-if="p.status === 'uploading'" class="preview-mask">
            <n-progress
              type="circle"
              :percentage="p.progress"
              :size="44"
              :show-indicator="true"
            />
          </div>
          <div v-else-if="p.status === 'done'" class="preview-mask done">✓</div>
          <div v-else-if="p.status === 'failed'" class="preview-mask failed" :title="p.errorMsg">
            ✕
          </div>
          <div v-else-if="p.status === 'duplicate'" class="preview-mask dup" :title="p.errorMsg">
            ⧉
          </div>
        </div>
      </div>

      <div class="upload-actions">
        <n-button type="primary" size="large" :loading="uploading" @click="startUpload">
          {{ uploading ? '导入中…' : '开始导入' }}
        </n-button>
        <n-text v-if="uploading" depth="3">
          已完成 {{ stats.done }} / {{ stats.total }}
        </n-text>
      </div>
    </n-card>

    <!-- 完成后跳转 -->
    <n-card v-if="uploadedSetId && !uploading && stats.done > 0" size="small" class="step-card">
      <n-space align="center" justify="space-between">
        <n-text>照片组已导入完成，可前往人物详情查看。</n-text>
        <n-button type="primary" secondary @click="goPersonDetail">查看人物照片组 →</n-button>
      </n-space>
    </n-card>

    <!-- 新建人物对话框 -->
    <PersonFormModal v-model:show="showForm" :person="null" @saved="onPersonCreated" />
  </div>
</template>

<style scoped>
.model-photo-page {
  max-width: 860px;
  margin: 0 auto;
  padding-bottom: 40px;
}

.step-card {
  margin-top: 16px;
}

/* 文件夹选择区域 */
.folder-zone {
  padding: 36px 24px;
  border: 2px dashed #d1d5db;
  border-radius: 12px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.folder-zone:hover {
  border-color: #818cf8;
  background: #fafafe;
}

.folder-icon {
  font-size: 48px;
}

.folder-title {
  margin: 8px 0 4px;
  font-size: 16px;
  font-weight: 600;
  color: #374151;
}

.folder-desc {
  margin: 0;
  font-size: 13px;
  color: #9ca3af;
}

/* 预览网格 */
.preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 10px;
}

.preview-item {
  position: relative;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  overflow: hidden;
  background: #eef1f6;
}

.preview-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.preview-index {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
}

.preview-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.55);
}

.preview-mask.done {
  background: rgba(16, 185, 129, 0.35);
  color: #065f46;
  font-size: 28px;
  font-weight: 700;
}

.preview-mask.failed {
  background: rgba(239, 68, 68, 0.35);
  color: #7f1d1d;
  font-size: 28px;
  font-weight: 700;
}

.preview-mask.dup {
  background: rgba(250, 204, 21, 0.4);
  color: #713f12;
  font-size: 28px;
  font-weight: 700;
}

.upload-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
}
</style>
