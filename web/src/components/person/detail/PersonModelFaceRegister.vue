<script setup lang="ts">
/** 模特人脸特征注册折叠面板（从写真照片组，Top-5 高质量人脸平均池化）。 */

import { computed } from 'vue'
import { modelsApi } from '@/api/persons'
import { useModelFaceRegister } from '@/composables/useModelFaceRegister'

const props = defineProps<{
  personId: number
  api: typeof modelsApi
}>()

const { modelFaceStatus, modelFaceBusy, handleRegisterModelFace } = useModelFaceRegister({
  personId: computed(() => props.personId),
  api: computed(() => props.api),
})
</script>

<template>
  <a-collapse class="face-register-collapse">
    <a-collapse-item key="model-face">
      <template #header>
        <span class="face-collapse-title">人脸特征注册</span>
      </template>
      <template #extra>
        <a-tag v-if="modelFaceStatus?.registered" color="green" size="small">
          已注册{{
            modelFaceStatus?.updated_at ? `（${modelFaceStatus.updated_at.slice(0, 10)}）` : ''
          }}
        </a-tag>
        <a-tag v-else color="orange" size="small">未注册</a-tag>
      </template>
      <p class="face-hint">
        从写真照片组自动挑选 Top-5 张高质量正脸照片提取特征平均池化入库；素材库中的人脸将
        自动与模特特征库匹配（需先运行「人脸库扫描」）。照片组更新后建议重新注册。
      </p>
      <div class="face-upload-row">
        <a-button
          size="small"
          type="primary"
          :loading="modelFaceBusy"
          @click="handleRegisterModelFace"
        >
          {{ modelFaceStatus?.registered ? '重新注册' : '从照片组注册' }}
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
</style>
