/** 上传偏好 composable：本地持久化自动分析、上传后跳转、去重选项。 */

import { ref } from 'vue'
import type { UploadAfterAction } from '@/types/upload'

export function useUploadPrefs() {
  /** 自动 AI 分析（localStorage 持久化，默认开启） */
  const autoAnalyze = ref(localStorage.getItem('upload-auto-analyze') !== 'false')
  /** 上传后跳转行为（默认留在本页） */
  const afterUpload = ref<UploadAfterAction>(
    (localStorage.getItem('upload-after') as UploadAfterAction) || 'stay'
  )
  /** 跳过重复文件（默认开启） */
  const skipDuplicates = ref(localStorage.getItem('upload-skip-duplicates') !== 'false')

  /** 保存偏好到 localStorage */
  function savePrefs() {
    localStorage.setItem('upload-auto-analyze', String(autoAnalyze.value))
    localStorage.setItem('upload-after', afterUpload.value)
    localStorage.setItem('upload-skip-duplicates', String(skipDuplicates.value))
  }

  return { autoAnalyze, afterUpload, skipDuplicates, savePrefs }
}
