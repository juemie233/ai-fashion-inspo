/** 剪贴板读写（设置页「复制后端地址 / 从剪贴板粘贴」用）。
 *
 * 为什么单独包一层：`expo-clipboard` 是新加的原生模块，旧 APK 里没有原生实现。
 * 这里按需加载，缺失时返回可读提示而不是让设置页崩掉（与 `mediaActions` 同一套做法）。
 */

/* eslint-disable @typescript-eslint/no-var-requires */

export interface ClipboardResult {
  ok: boolean
  message: string
}

interface ClipboardModule {
  setStringAsync: (text: string) => Promise<boolean>
  getStringAsync: () => Promise<string>
}

/** 原生模块缺失时的提示（旧 APK 未重建会出现） */
const MODULE_MISSING = '当前版本不支持该功能，请更新 App 后重试'

function loadClipboard(): ClipboardModule | null {
  try {
    return require('expo-clipboard') as ClipboardModule
  } catch {
    return null
  }
}

/** 复制文本到剪贴板 */
export async function copyText(text: string): Promise<ClipboardResult> {
  const value = (text ?? '').trim()
  if (!value) return { ok: false, message: '没有可复制的内容' }

  const clipboard = loadClipboard()
  if (!clipboard) return { ok: false, message: MODULE_MISSING }

  try {
    await clipboard.setStringAsync(value)
    return { ok: true, message: '已复制到剪贴板' }
  } catch {
    return { ok: false, message: '复制失败，请手动选择文本复制' }
  }
}

/** 读剪贴板文本（空/空白返回空串） */
export async function readClipboard(): Promise<string> {
  const clipboard = loadClipboard()
  if (!clipboard) return ''
  try {
    const value = await clipboard.getStringAsync()
    return typeof value === 'string' ? value.trim() : ''
  } catch {
    return ''
  }
}
