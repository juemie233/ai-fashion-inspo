/** 博主 CSV 导入逻辑：上传 → 结果提示 → 刷新列表。 */

import { ref } from 'vue'
import { Message, type RequestOption, type UploadRequest } from '@arco-design/web-vue'
import { importBloggersCsv } from '@/api/persons'
import { usePersonsStore, type PersonKind } from '@/stores/persons'
import type { PersonImportResult } from '@shared/types/person'

export function useBloggerImport(kind: PersonKind) {
  const store = usePersonsStore(kind)

  const importResult = ref<PersonImportResult | null>(null)
  const importError = ref('')

  /** 处理 CSV 导入（a-upload custom-request）：上传 → 展示结果 → 刷新列表。
   * Arco 的 custom-request 期望同步返回（UploadRequest），异步逻辑用内部 IIFE 包裹。 */
  function handleImportCsv(options: RequestOption): UploadRequest {
    void (async () => {
      const file = options.fileItem.file
      importResult.value = null
      importError.value = ''
      if (!file) {
        Message.error('未获取到文件，请重新选择')
        return
      }
      try {
        const result = await importBloggersCsv(file)
        importResult.value = result
        if (result.failed > 0) {
          Message.warning(
            `导入完成：新增 ${result.imported}，更新 ${result.updated}，失败 ${result.failed}`,
          )
        } else {
          Message.success(`导入成功：新增 ${result.imported}，更新 ${result.updated}`)
        }
        await store.reload()
        options.onSuccess?.()
      } catch (e) {
        const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        importError.value = detail || '导入失败'
        Message.error(importError.value)
        options.onError?.(e as Error)
      }
    })()
    return {}
  }

  /** 关闭导入结果提示 */
  function dismissImportResult() {
    importResult.value = null
    importError.value = ''
  }

  return { importResult, importError, handleImportCsv, dismissImportResult }
}
