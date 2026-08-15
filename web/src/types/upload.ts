/** 上传页共享类型定义。 */

/** 上传队列项 */
export interface UploadQueueItem {
  id: string
  file: File
  thumbnail: string // object URL
  status: 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'
  progress: number
  resultId?: string
  errorMsg?: string
}

/** 最近上传记录 */
export interface RecentUpload {
  id: string
  thumbnailPath: string | null
  filePath: string
  mediaType?: string
}

/** 上传后跳转行为 */
export type UploadAfterAction = 'stay' | 'detail' | 'home'
