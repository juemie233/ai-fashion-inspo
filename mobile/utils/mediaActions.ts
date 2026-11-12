/** 保存到相册 / 系统分享（P0-3）。
 *
 * 三条硬约束：
 * 1. **按需加载原生模块**：`expo-media-library` / `expo-sharing` / `expo-file-system`
 *    是新加的原生模块，旧 APK 里没有对应原生实现。用 `require` 包起来，缺失时返回
 *    可读提示而不是让整页崩掉（用户没重建 APK 时仍能看图/缩放）。
 * 2. **远程图片先落盘**：素材地址是 `http://后端/api/files/...`，而保存/分享都只认
 *    本地 `file://`；先下载到缓存目录，用完（`finally`）删掉，不留垃圾。
 * 3. **错误一律转成中文可读文案**：调用方直接展示 `message`，不需要判断异常类型。
 */

/* eslint-disable @typescript-eslint/no-var-requires */

/** 操作结果（永不抛异常） */
export interface MediaActionResult {
  ok: boolean
  message: string
}

/** 原生模块缺失时的提示（旧 APK 未重建会出现） */
const MODULE_MISSING = '当前版本不支持该功能，请更新 App 后重试'

/** 下载后的临时文件前缀（放缓存目录，系统可回收） */
const TEMP_PREFIX = 'share-'

interface FileSystemModule {
  cacheDirectory: string | null
  downloadAsync: (
    url: string,
    target: string,
  ) => Promise<{ status: number; uri: string }>
  deleteAsync: (uri: string, options?: { idempotent?: boolean }) => Promise<void>
}

interface MediaLibraryModule {
  requestPermissionsAsync: () => Promise<{ granted: boolean }>
  saveToLibraryAsync: (uri: string) => Promise<void>
}

interface SharingModule {
  isAvailableAsync: () => Promise<boolean>
  shareAsync: (uri: string, options?: Record<string, unknown>) => Promise<void>
}

/** 安全 require：模块不存在（未重建 APK）时返回 null */
function tryRequire<T>(loader: () => T): T | null {
  try {
    return loader()
  } catch {
    return null
  }
}

function loadFileSystem(): FileSystemModule | null {
  return tryRequire(() => require('expo-file-system') as FileSystemModule)
}

function loadMediaLibrary(): MediaLibraryModule | null {
  return tryRequire(() => require('expo-media-library') as MediaLibraryModule)
}

function loadSharing(): SharingModule | null {
  return tryRequire(() => require('expo-sharing') as SharingModule)
}

/** 把素材地址准备成本地文件：已经是本地文件则原样返回 */
async function ensureLocalFile(
  uri: string,
): Promise<{ path: string; cleanup?: () => Promise<void> } | { error: string }> {
  if (uri.startsWith('file://')) return { path: uri }

  const fs = loadFileSystem()
  if (!fs || !fs.cacheDirectory) return { error: MODULE_MISSING }

  const extension = /\.(png|webp|gif|heic)(?:\?|$)/i.exec(uri)?.[1]?.toLowerCase() ?? 'jpg'
  const target = `${fs.cacheDirectory}${TEMP_PREFIX}${Date.now()}.${extension}`
  try {
    const result = await fs.downloadAsync(uri, target)
    if (result.status !== 200) {
      await fs.deleteAsync(target, { idempotent: true }).catch(() => {})
      return { error: `下载失败（HTTP ${result.status}），请检查网络后重试` }
    }
    return {
      path: result.uri,
      cleanup: async () => {
        await fs.deleteAsync(target, { idempotent: true }).catch(() => {})
      },
    }
  } catch {
    return { error: '下载失败，请检查网络后重试' }
  }
}

/**
 * 保存图片到相册（需要相册写权限）。
 *
 * @param uri 素材地址（远程 http 或本地 file）
 */
export async function saveImageToLibrary(uri: string): Promise<MediaActionResult> {
  const library = loadMediaLibrary()
  if (!library) return { ok: false, message: MODULE_MISSING }

  try {
    const permission = await library.requestPermissionsAsync()
    if (!permission.granted) {
      return { ok: false, message: '没有相册权限，请在系统设置中允许后重试' }
    }

    const local = await ensureLocalFile(uri)
    if ('error' in local) return { ok: false, message: local.error }

    try {
      await library.saveToLibraryAsync(local.path)
      return { ok: true, message: '已保存到相册' }
    } finally {
      await local.cleanup?.()
    }
  } catch {
    return { ok: false, message: '保存失败，请稍后重试' }
  }
}

/**
 * 调用系统分享面板分享图片。
 *
 * @param uri 素材地址（远程 http 或本地 file）
 */
export async function shareImage(uri: string): Promise<MediaActionResult> {
  const sharing = loadSharing()
  if (!sharing) return { ok: false, message: MODULE_MISSING }

  try {
    if (!(await sharing.isAvailableAsync())) {
      return { ok: false, message: '当前设备不支持系统分享' }
    }

    const local = await ensureLocalFile(uri)
    if ('error' in local) return { ok: false, message: local.error }

    try {
      await sharing.shareAsync(local.path, {
        mimeType: 'image/jpeg',
        dialogTitle: '分享素材',
        UTI: 'public.jpeg',
      })
      return { ok: true, message: '已打开分享面板' }
    } finally {
      await local.cleanup?.()
    }
  } catch {
    return { ok: false, message: '分享失败，请稍后重试' }
  }
}
