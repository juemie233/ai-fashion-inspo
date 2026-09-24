/** 人脸库扫描页的媒体地址工具：缩略图 / 悬停大图 / 人物头像 / 聚合组代表图。
 *
 * 为什么独立成 util：模板已按 tab 拆到 `components/person/face/*`，这些地址换算被
 * 3~4 个子组件同时需要；留在视图里要么四处重复、要么靠 props 传函数。
 * 全是纯函数（只依赖传入的检测项/人物项），可直接单测。
 */

import { getFileUrl } from '@/api/inspirations'
import type { DetectionItem, FaceClusterGroup, PersonAggregateItem } from '@/api/faceScan'

/** 视频文件后缀：media_type 缺失时的兜底判据（后端漏字段时不至于把 mp4 当图片加载） */
const VIDEO_EXT_RE = /\.(mp4|mov|webm|m4v|avi|mkv)$/i

/** 媒体类型判定：以 media_type 为准，缺失时回退看路径后缀。
 *
 * 后端应在每个明细接口回传 media_type；此处兜底是因为「接口新增字段时漏传」已经
 * 真实发生过一次（聚合分组明细）——结果是悬停大图拿 mp4 去当 <img> 加载，浮层空白。
 */
function isVideoMedia(
  mediaType: string | null | undefined,
  filePath: string | null | undefined,
): boolean {
  if (mediaType) return mediaType === 'video'
  return VIDEO_EXT_RE.test(filePath ?? '')
}

/** 素材是否视频：media_type 为 video 时 file_path 是 mp4，不能当 <img> 加载 */
export function isVideoItem(item: DetectionItem): boolean {
  return isVideoMedia(item.media_type, item.file_path)
}

/** 缩略图地址（优先缩略图；视频素材绝不回退到 file_path(mp4)，否则 <img> 必破图） */
export function thumbUrl(item: DetectionItem): string {
  if (isVideoItem(item)) return item.thumbnail_path ? getFileUrl(item.thumbnail_path) : ''
  return getFileUrl(item.thumbnail_path || item.file_path)
}

/** 悬停大图地址：图片用原图；视频没有可预览的静态大图，用缩略图大图（避免破图） */
export function largePreviewUrl(item: DetectionItem): string {
  if (isVideoItem(item)) return item.thumbnail_path ? getFileUrl(item.thumbnail_path) : ''
  return getFileUrl(item.file_path)
}

/** 人物头像地址：只有手动设置一条来源（未设置则显示首字占位，与人物列表/详情一致） */
export function personAvatarUrl(item: PersonAggregateItem): string | undefined {
  return item.avatar_path ? getFileUrl(item.avatar_path) : undefined
}

/** 聚合分组代表图地址（优先缩略图；视频素材绝不回退到 file_path(mp4)） */
export function groupThumbUrl(group: FaceClusterGroup): string {
  const isVideo = isVideoMedia(group.rep_media_type, group.rep_file_path)
  if (isVideo) return group.rep_thumbnail_path ? getFileUrl(group.rep_thumbnail_path) : ''
  const path = group.rep_thumbnail_path || group.rep_file_path
  return path ? getFileUrl(path) : ''
}
