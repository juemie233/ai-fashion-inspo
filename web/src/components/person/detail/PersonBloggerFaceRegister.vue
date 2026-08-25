<script setup lang="ts">
/** 博主人脸特征注册折叠面板：上传照片 与/或 从已关联素材选择（合计 1~5 张）。 */

import { computed } from 'vue'
import { getFileUrl } from '@/api/inspirations'
import { bloggersApi } from '@/api/persons'
import { useBloggerFaceRegister } from '@/composables/useBloggerFaceRegister'

const props = defineProps<{
  personId: number
  api: typeof bloggersApi
}>()

const {
  faceStatus,
  faceTab,
  faceFileList,
  faceUploading,
  faceInspItems,
  faceInspTotal,
  faceInspPage,
  faceInspPageSize,
  faceInspLoading,
  selectedFaceInspIds,
  loadFaceInspirations,
  toggleFaceInsp,
  handleRegisterFace,
} = useBloggerFaceRegister({
  personId: computed(() => props.personId),
  api: computed(() => props.api),
})

/** 已选照片 + 素材合计数量（注册按钮禁用判断） */
const selectedTotal = computed(
  () => faceFileList.value.filter((f) => !!f.file).length + selectedFaceInspIds.value.size,
)
</script>

<template>
  <a-collapse class="face-register-collapse">
    <a-collapse-item key="face">
      <template #header>
        <span class="face-collapse-title">人脸特征注册</span>
      </template>
      <template #extra>
        <a-tag v-if="faceStatus?.registered" color="green" size="small">
          已注册{{ faceStatus?.updated_at ? `（${faceStatus.updated_at.slice(0, 10)}）` : '' }}
        </a-tag>
        <a-tag v-else color="orange" size="small">未注册</a-tag>
      </template>
      <p class="face-hint">
        上传正脸照片或从已关联素材中选择图片（两种来源合计 1~5 张），系统提取人脸特征并
        平均池化入库；素材库中的人脸将自动与特征库匹配。重复注册将覆盖旧特征（重新注册）。
      </p>

      <a-tabs v-model:active-key="faceTab" size="small" type="line">
        <!-- Tab1：上传照片（原有方式） -->
        <a-tab-pane key="upload" title="上传照片">
          <a-upload
            v-model:file-list="faceFileList"
            multiple
            :max="5"
            accept="image/*"
            list-type="picture-card"
            show-remove-button
          >
            <a-button size="small">选择照片（最多 5 张）</a-button>
          </a-upload>
        </a-tab-pane>

        <!-- Tab2：从已关联素材中选择图片 -->
        <a-tab-pane key="inspiration" title="从素材选择">
          <div class="face-insp-grid">
            <div
              v-for="item in faceInspItems"
              :key="item.inspiration_id"
              class="face-insp-item"
              :class="{ checked: selectedFaceInspIds.has(item.inspiration_id) }"
              :title="item.inspiration_id"
              @click="toggleFaceInsp(item.inspiration_id)"
            >
              <img
                :src="getFileUrl(item.thumbnail_path || item.file_path)"
                :alt="item.inspiration_id"
                loading="lazy"
              />
              <div v-if="selectedFaceInspIds.has(item.inspiration_id)" class="face-insp-check">
                ✓
              </div>
            </div>
            <a-empty
              v-if="!faceInspLoading && faceInspItems.length === 0"
              description="暂无已关联素材，可先上传素材并关联该博主"
              size="small"
              style="grid-column: 1 / -1; padding: 16px 0"
            />
            <div v-if="faceInspLoading" class="face-insp-loading">
              <a-spin :size="14" />
              <span>加载中...</span>
            </div>
          </div>
          <a-pagination
            v-if="faceInspTotal > faceInspPageSize"
            style="margin-top: 10px; justify-content: center"
            :current="faceInspPage"
            :page-size="faceInspPageSize"
            :total="faceInspTotal"
            @change="loadFaceInspirations"
          />
        </a-tab-pane>
      </a-tabs>

      <!-- 注册按钮（上传照片 + 勾选素材合并提交） -->
      <div class="face-upload-row" style="margin-top: 10px; justify-content: space-between">
        <a-typography-text type="secondary" style="font-size: 12px">
          已选：{{ faceFileList.filter((f) => !!f.file).length }} 张照片 +
          {{ selectedFaceInspIds.size }} 张素材（合计 ≤ 5）
        </a-typography-text>
        <a-button
          size="small"
          type="primary"
          :loading="faceUploading"
          :disabled="selectedTotal === 0"
          @click="handleRegisterFace"
        >
          {{ faceStatus?.registered ? '重新注册' : '注册人脸' }}
        </a-button>
      </div>
    </a-collapse-item>
  </a-collapse>
</template>

<style scoped>
.face-register-collapse {
  margin-bottom: 12px;
}
.face-collapse-title {
  font-size: 15px;
  font-weight: 600;
}
.face-hint {
  margin: 8px 0;
  font-size: 12px;
  color: #999;
}
.face-upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 人脸注册素材选择网格：缩略图 + 勾选角标 */
.face-insp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 10px;
  min-height: 60px;
}

.face-insp-item {
  position: relative;
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid transparent;
  transition:
    border-color 0.15s,
    opacity 0.15s;
  aspect-ratio: 3 / 4;
}

.face-insp-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  background: #f5f5f5;
}

.face-insp-item.checked {
  border-color: #18a058;
}

.face-insp-item.checked img {
  opacity: 0.75;
}

.face-insp-check {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #18a058;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.face-insp-loading {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px 0;
  color: #999;
  font-size: 12px;
}
</style>
