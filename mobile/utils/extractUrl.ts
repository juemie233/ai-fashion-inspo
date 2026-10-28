/** 从分享文本里提取第一个 http(s) 链接。
 *
 * 小红书 / 抖音的分享文案形如：
 * 「7.88 复制打开抖音，看看【xxx】... https://v.douyin.com/xxxx/」
 * 正文里可能夹带表情与换行，所以用正则找第一个 URL，并去掉尾部标点。
 */

/** 从文本中提取第一个 URL；没有则返回 null */
export function extractUrl(text: string | null | undefined): string | null {
  if (!text || typeof text !== 'string') return null
  // 关键：分享文案常常是「链接紧贴中文」（…/abc123/，谢谢），所以必须把中文和
  // 常见的中文标点一起排除，否则会把「，谢谢」也算进 URL
  const match = text.match(/https?:\/\/[^\s\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"'<>]+/i)
  if (!match) return null
  // 去掉中文/英文标点尾巴（分享文案常见「链接」+ 标点）
  return match[0].replace(/[，。、；：！？,.;:!?]+$/, '')
}

/** 判断文本是否像纯图片直链（用于给出更准确的提示） */
export function looksLikeImageUrl(url: string | null): boolean {
  if (!url) return false
  return /\.(jpe?g|png|webp|gif|bmp)(\?.*)?$/i.test(url)
}
