/** 素材来源类型中文映射工具。 */

/** 来源类型 → 中文显示文案 */
export const SOURCE_TYPE_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  scraper: '自动采集',
  manual_upload: '手动上传',
  generated: 'AI 生成',
  browser_extension: '浏览器插件',
  batch_import: '目录导入',
  url_import: '链接导入',
}

/** 来源类型中文映射，缺失时回退原值。
 *
 * 入参按 unknown 处理：后端字段脏掉（对象/数组/数字）时若原样返回，会被塞进 <Text>，
 * React 直接抛 "Objects are not valid as a React child" → 整棵树卸载（白屏）。
 */
export function sourceLabel(type: unknown): string {
  if (typeof type !== 'string') return '未知来源'
  return SOURCE_TYPE_LABELS[type] || type
}
