/** 媒体文件类型工具。 */

const VIDEO_EXTS = /\.(mp4|mov|webm|m4v)$/i

/** 判断文件是否为视频（按 MIME 类型与扩展名，两处上传组件共用） */
export function isVideoFile(file: File): boolean {
  return file.type.startsWith('video/') || VIDEO_EXTS.test(file.name)
}
