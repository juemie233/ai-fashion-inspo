/** 模特人脸特征注册逻辑（从写真照片组，Top-5 高质量人脸平均池化）。 */

import { ref, watch, type ComputedRef, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { modelsApi, type ModelFaceStatus } from '@/api/persons'

interface Options {
  personId: Ref<number>
  api: ComputedRef<typeof modelsApi>
}

export function useModelFaceRegister({ personId, api }: Options) {
  const modelFaceStatus = ref<ModelFaceStatus | null>(null)
  const modelFaceBusy = ref(false)

  async function loadModelFaceStatus() {
    try {
      modelFaceStatus.value = await api.value.fetchFaceStatus(personId.value)
    } catch {
      // 人脸状态加载失败不阻塞详情页
    }
  }

  /** 从照片组注册/重新注册模特人脸（重复注册覆盖旧特征） */
  async function handleRegisterModelFace() {
    modelFaceBusy.value = true
    try {
      const r = await api.value.registerFace(personId.value, 5)
      modelFaceStatus.value = {
        registered: true,
        model_id: r.model_id,
        updated_at: r.updated_at,
      }
      const warningText = r.warnings?.length ? `，${r.warnings.length} 条照片跳过警告` : ''
      Message.success(`已从照片组注册人脸（使用 ${r.photos_used ?? 0} 张高质量照片${warningText}）`)
    } catch (e) {
      Message.error(getApiErrorMessage(e, '注册失败'))
    } finally {
      modelFaceBusy.value = false
    }
  }

  watch(
    personId,
    () => {
      modelFaceStatus.value = null
      loadModelFaceStatus()
    },
    { immediate: true },
  )

  return { modelFaceStatus, modelFaceBusy, loadModelFaceStatus, handleRegisterModelFace }
}
