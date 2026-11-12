/** 分享入库的结果归纳（P1 分享 Intent 扩展）。
 *
 * 分享进来的可能是「多张图」「一条带链接的文本」「纯文字」，每条结果分三种：
 * 入库成功 / 已在库中（后端 409 内容重复）/ 失败（网络、格式、权限）。
 * 这里把三者归纳成一句可读的标题 + 明细，避免一次分享 5 张就弹 5 个框。
 *
 * 为什么放 utils：这些是纯函数，能直接单测；组件只负责「按顺序调接口、收集计数」。
 */

/** 单次分享最多处理多少个文件（防止误选整本相册把 App/后端打满） */
export const MAX_SHARE_FILES = 12

/** 归纳用的计数 */
export interface ShareOutcome {
  /** 入库/导入成功数 */
  created: number
  /** 因内容重复被后端拒绝（已在库中）的数量 */
  duplicate: number
  /** 其它失败数量 */
  failed: number
  /** 超出单次上限、没有处理的数量 */
  skipped?: number
  /** 第一条失败原因（用于明细里说明「为什么失败」） */
  firstError?: string
}

/** 结果弹窗的标题与正文 */
export interface ShareSummary {
  title: string
  message: string
}

/** 是否「已在库中」：后端内容去重返回 409，detail 形如「该素材已存在（内容重复）」 */
export function isDuplicateError(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  if (response?.status === 409) return true
  const detail = response?.data?.detail
  return typeof detail === 'string' && /已存在|已在库中/.test(detail)
}

/** 从接口错误里取出可读原因（后端 detail 优先，否则给通用网络提示） */
export function describeShareError(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  return '请检查后端地址与网络后重试'
}

/**
 * 把计数归纳成弹窗文案。
 *
 * @param mode `image` 分享的是图片文件，`url` 分享的是链接（措辞用「张」/「个链接」）
 */
export function buildShareSummary(
  mode: 'image' | 'url',
  outcome: ShareOutcome,
): ShareSummary {
  const { created, duplicate, failed, skipped = 0, firstError } = outcome
  const unit = mode === 'image' ? '张' : '个链接'
  const lines: string[] = []

  if (created > 0) {
    lines.push(mode === 'image' ? `已入库 ${created} 张，AI 将自动分析标签` : `已导入 ${created} 个链接`)
  }
  if (duplicate > 0) {
    lines.push(`${duplicate} ${unit}已在库中（内容重复），已跳过`)
  }
  if (failed > 0) {
    lines.push(`${failed} ${unit}失败：${firstError || '请稍后重试'}`)
  }
  if (skipped > 0) {
    lines.push(`另有 ${skipped} ${unit}超出单次上限（${MAX_SHARE_FILES}），未处理`)
  }

  const onlyDuplicate = duplicate > 0 && created === 0 && failed === 0
  const onlySuccess = created > 0 && duplicate === 0 && failed === 0 && skipped === 0
  const allFailed = failed > 0 && created === 0 && duplicate === 0

  const title = onlyDuplicate
    ? '已在库中'
    : onlySuccess
      ? mode === 'image'
        ? '已入库'
        : '已导入'
      : allFailed
        ? '入库失败'
        : '分享入库结果'

  return { title, message: lines.join('\n') }
}

/**
 * 从分享文本里取「标题」：去掉链接与常见分享后缀，最多 24 字。
 *
 * 抖音/小红书文案形如「7.88 复制打开抖音，看看【xxx】... https://v.douyin.com/abc/」，
 * 标题只用于成功提示里让用户确认「入库的是哪一条」，不做存储（后端 from-url 没有 title 字段）。
 */
export function extractShareTitle(text: string | null | undefined, url: string | null): string {
  if (!text || typeof text !== 'string') return ''
  let rest = text
  if (url) rest = rest.split(url).join(' ')
  rest = rest
    .replace(/复制打开抖音，?/g, ' ')
    .replace(/复制本条信息，?打开.*?查看精彩内容/g, ' ')
    .replace(/[【】\[\]]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  // 去掉纯数字/口令前缀（如「7.88」）与「看看/分享」这类动词引导词
  rest = rest
    .replace(/^[\d.]+\s*/, '')
    .replace(/^看看\s*/, '')
    .trim()
  return rest.slice(0, 24)
}
