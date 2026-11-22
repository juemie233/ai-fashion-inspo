/** 剪贴板工具：复制文本（优先异步 Clipboard API，回退 execCommand），返回是否成功。 */

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
    throw new Error('clipboard api 不可用，走回退方案')
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.cssText = 'position:fixed;left:-9999px'
      document.body.appendChild(ta)
      try {
        ta.select()
        return document.execCommand('copy')
      } finally {
        // execCommand 不存在/抛异常时也必须清理，否则页面残留隐藏 textarea
        document.body.removeChild(ta)
      }
    } catch {
      return false
    }
  }
}
