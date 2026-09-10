/**
 * 站内导航辅助：在新浏览器标签页打开页面（不离开当前页）。
 *
 * ⚠️ 不要写成 `window.open(href, '_blank', 'noopener,noreferrer')`——
 * 按 HTML 规范，features 含 `noopener` 时 `window.open` **返回 null**，
 * 调用方无法区分「成功打开新标签」与「被浏览器弹窗拦截」，
 * 若据此降级为当前页跳转，会出现「新标签打开了、当前页也跳走」的双跳问题。
 *
 * 正确做法：不带 noopener 打开，拿到窗口对象后手动切断 opener 引用
 * （达到同样的安全效果），返回值即可可靠表示是否真的打开了。
 */

/** 在新标签页打开 href；返回是否成功打开（false = 被浏览器拦截）。 */
export function openInNewTab(href: string): boolean {
  const win = window.open(href, '_blank')
  if (!win) return false
  // 切断新页面对当前页的 window.opener 引用（等价 noopener 的安全效果）
  win.opener = null
  return true
}
