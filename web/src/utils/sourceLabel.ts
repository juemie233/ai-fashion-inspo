/** 素材来源类型中文映射工具。 */

/** 来源类型 → 中文显示文案 */
export const SOURCE_TYPE_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  scraper: '自动采集',
  manual_upload: '手动上传',
  browser_extension: '浏览器插件',
  batch_import: '目录导入',
  url_import: '链接导入',
}

/** 来源类型中文映射，缺失时回退原值 */
export function sourceLabel(type: string): string {
  return SOURCE_TYPE_LABELS[type] || type
}

/**
 * 生成来源类型筛选选项（首个为「全部」项）。
 * 各处筛选器统一由此生成，新增来源类型时只改 SOURCE_TYPE_LABELS 一处。
 */
export function buildSourceOptions(
  allValue: string,
  allLabel = '全部来源',
): { label: string; value: string }[] {
  return [
    { label: allLabel, value: allValue },
    ...Object.entries(SOURCE_TYPE_LABELS).map(([value, label]) => ({ label, value })),
  ]
}
