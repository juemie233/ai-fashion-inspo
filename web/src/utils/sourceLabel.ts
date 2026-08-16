/** 素材来源类型中文映射工具。 */

/** 来源类型 → 中文显示文案 */
export const SOURCE_TYPE_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  scraper: '自动采集',
  manual_upload: '手动上传',
  browser_extension: '浏览器插件',
  batch_import: '目录导入',
}

/** 来源类型中文映射，缺失时回退原值 */
export function sourceLabel(type: string): string {
  return SOURCE_TYPE_LABELS[type] || type
}
