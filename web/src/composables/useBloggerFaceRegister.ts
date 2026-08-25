/** 博主人脸特征注册逻辑：上传照片 与/或 从已关联素材中选择图片（合计 1~5 张）。 */

import { ref, watch, type ComputedRef, type Ref } from 'vue'
import { Message, type FileItem } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { bloggersApi, type PersonInspiration } from '@/api/persons'

/** 照片与素材合计上限（后端同款限制） */
const FACE_MAX_TOTAL = 5

interface Options {
  personId: Ref<number>
  api: ComputedRef<typeof bloggersApi>
}

export function useBloggerFaceRegister({ personId, api }: Options) {
  const faceStatus = ref<{ registered: boolean; updated_at?: string | null } | null>(null)
  /** 人脸注册来源选项卡：upload 上传照片 / inspiration 从素材选择 */
  const faceTab = ref<'upload' | 'inspiration'>('upload')
  /** 已选正脸照片（UploadFileInfo 结构：支持多选/缩略图预览/单张删除） */
  const faceFileList = ref<FileItem[]>([])
  const faceUploading = ref(false)

  // ── 素材选择状态（Tab2：该博主已关联素材的缩略图网格，勾选参与注册）──
  const faceInspItems = ref<PersonInspiration[]>([])
  const faceInspTotal = ref(0)
  const faceInspPage = ref(1)
  const faceInspPageSize = 30
  const faceInspLoading = ref(false)
  /** 已勾选的素材 ID（限制最多 5 张，与上传照片合计不超过 5） */
  const selectedFaceInspIds = ref<Set<string>>(new Set())

  /** 加载该博主已关联素材（分页，供人脸注册选择） */
  async function loadFaceInspirations(page: number = 1) {
    faceInspLoading.value = true
    try {
      const data = await api.value.fetchInspirations(personId.value, page, faceInspPageSize)
      faceInspItems.value = data.items ?? []
      faceInspTotal.value = data.total ?? 0
      faceInspPage.value = page
    } catch {
      Message.error('加载素材失败')
    } finally {
      faceInspLoading.value = false
    }
  }

  /** 勾选/取消素材（最多 5 张，超出提示） */
  function toggleFaceInsp(id: string) {
    const next = new Set(selectedFaceInspIds.value)
    if (next.has(id)) {
      next.delete(id)
    } else {
      const uploadCount = faceFileList.value.filter((f) => !!f.file).length
      if (next.size + uploadCount >= FACE_MAX_TOTAL) {
        Message.warning('照片与素材合计最多 5 张')
        return
      }
      next.add(id)
    }
    selectedFaceInspIds.value = next
  }

  async function loadFaceStatus() {
    try {
      faceStatus.value = await api.value.fetchFaceStatus(personId.value)
    } catch {
      // 人脸状态加载失败不阻塞详情页
    }
  }

  /** 注册 / 重新注册博主人脸（上传照片 + 已选素材可混合；重复注册覆盖旧特征） */
  async function handleRegisterFace() {
    const files = faceFileList.value.map((f) => f.file).filter((f): f is File => !!f)
    const selectedIds = [...selectedFaceInspIds.value]
    if (files.length === 0 && selectedIds.length === 0) {
      Message.warning('请选择照片或勾选素材（合计 1~5 张）')
      return
    }
    if (files.length + selectedIds.length > FACE_MAX_TOTAL) {
      Message.warning('照片与素材合计最多 5 张')
      return
    }
    faceUploading.value = true
    try {
      const r = await api.value.registerFace(personId.value, files, selectedIds)
      const skipped = (r.photo_results ?? []).filter((p) => p.status === 'skipped')
      const sourceLabel = (p: { source?: string }) => (p.source === 'inspiration' ? '素材' : '照片')
      let detail = ''
      if (skipped.length > 0) {
        detail =
          '；已跳过：' +
          skipped
            .map((p) => `第${p.index}张${sourceLabel(p)}：${p.message ?? '未检出清晰人脸'}`)
            .join('；')
      }
      const warnings = r.warnings ?? []
      if (warnings.length > 0) {
        detail += `；${warnings.join('；')}`
      }
      if (detail) {
        Message.warning({
          content: `注册成功（${r.photos_used ?? 0}/${r.photos_total ?? 0} 张图片检出人脸）${detail}`,
          duration: 8000,
        })
      } else {
        Message.success(
          `人脸注册成功（${r.photos_used ?? 0}/${r.photos_total ?? 0} 张图片检出人脸）`,
        )
      }
      faceFileList.value = []
      selectedFaceInspIds.value = new Set()
      await loadFaceStatus()
    } catch (e) {
      Message.error(getApiErrorMessage(e, '人脸注册失败'))
    } finally {
      faceUploading.value = false
    }
  }

  /** 人物切换时重置人脸注册的素材选择状态 */
  function reset() {
    faceTab.value = 'upload'
    faceFileList.value = []
    faceInspItems.value = []
    faceInspPage.value = 1
    selectedFaceInspIds.value = new Set()
  }

  /** 切换到「从素材选择」Tab 时首次加载该博主素材 */
  watch(faceTab, (tab) => {
    if (tab === 'inspiration' && faceInspItems.value.length === 0) {
      loadFaceInspirations(1)
    }
  })

  watch(
    personId,
    () => {
      reset()
      loadFaceStatus()
    },
    { immediate: true },
  )

  return {
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
    loadFaceStatus,
    handleRegisterFace,
  }
}
