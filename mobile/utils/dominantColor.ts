/** 取素材主色，用于图片加载前的占位底色（避免白/灰块闪一下）。
 *
 * 后端 `dominant_colors` 存的是 JSON 数组字符串，元素**可能带中文后缀**
 * （AI 提示词要求输出 `#2B3A67 藏青` 这种格式，见 `backend/app/services/ai_service/analyze.py`
 * 的 `_update_dominant_colors`：只剥注释与括号，不保证纯十六进制）。
 * 所以这里按正则从每个元素里抠第一个十六进制色值，抠不到就换下一个。
 */

/** 匹配 #RGB / #RRGGBB（\b 收尾，避免把 #2B3A67藏 后面的字符吃进来） */
const HEX_PATTERN = /#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/

/** 把 3 位缩写展开成 6 位 */
function expand(hex: string): string {
  const value = hex.slice(1)
  if (value.length === 6) return `#${value.toUpperCase()}`
  return `#${value
    .split('')
    .map((c) => c + c)
    .join('')
    .toUpperCase()}`
}

/**
 * 从 `dominant_colors` 字段里取第一个可用颜色。
 *
 * @param raw 后端字段（JSON 数组字符串，可能为 null / 脏数据）
 * @returns `#RRGGBB` 或 null（调用方用主题兜底色）
 */
export function dominantColor(raw: unknown): string | null {
  if (typeof raw !== 'string' || raw.trim() === '') return null
  let items: unknown[] | null = null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) items = parsed
  } catch {
    // 不是合法 JSON：退化成整串直接匹配（历史数据里出现过裸字符串）
    items = null
  }
  const candidates = items ?? [raw]
  for (const item of candidates) {
    if (typeof item !== 'string') continue
    const match = HEX_PATTERN.exec(item)
    if (match) return expand(match[0])
  }
  return null
}
