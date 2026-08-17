/** 采集任务关键词工具：从历史任务中提取最近使用过的关键词。 */

/** 任务条目中与关键词提取相关的字段 */
export interface KeywordTaskLike {
  status: string
  config: string | null
}

/**
 * 从采集任务列表中提取「已完成任务」使用过的关键词（按任务最近优先去重）。
 *
 * 供新建采集任务表单做历史关键词候选：config 为 JSON 字符串，
 * 解析失败或字段缺失的条目静默跳过（历史脏数据不影响）。
 */
export function extractHistoryKeywords(tasks: KeywordTaskLike[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const t of tasks) {
    if (t.status !== 'completed') continue
    let keywords: unknown = null
    try {
      const config = t.config ? (JSON.parse(t.config) as Record<string, unknown>) : null
      keywords = config?.keywords
    } catch {
      continue // 脏配置无法解析，跳过
    }
    if (!Array.isArray(keywords)) continue
    for (const k of keywords) {
      if (typeof k !== 'string') continue
      const word = k.trim()
      if (word && !seen.has(word)) {
        seen.add(word)
        result.push(word)
      }
    }
  }
  return result
}
