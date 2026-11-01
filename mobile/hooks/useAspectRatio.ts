/** 图片宽高比 hook：首帧先用缓存/兜底，图片 `onLoad` 后按真实比例修正。
 *
 * 用法：
 * ```tsx
 * const { ratio, onLoad } = useAspectRatio(uri, 0.8)
 * <Image source={{uri}} onLoad={onLoad} style={{ aspectRatio: ratio }} />
 * ```
 * 比例会写进 `utils/imageAspect` 的缓存，同一张图第二次打开直接命中，不再跳一下。
 */

import { useCallback, useEffect, useState } from 'react'
import {
  cacheAspectRatio,
  getCachedAspectRatio,
  ratioFromSize,
} from '../utils/imageAspect'

/** expo-image / RN Image 的 onLoad 事件里我们只关心这两项 */
interface LoadEvent {
  source?: { width?: number; height?: number } | null
}

/**
 * @param uri 图片地址（空值直接返回兜底比例）
 * @param fallback 还没量出真实比例时的兜底比例（默认 0.8 = 4:5）
 */
export function useAspectRatio(
  uri: string | null | undefined,
  fallback = 0.8,
): { ratio: number; onLoad: (event: LoadEvent) => void } {
  const [ratio, setRatio] = useState<number>(() => getCachedAspectRatio(uri) ?? fallback)

  // 换图时（详情页连续切相似素材）先回到缓存/兜底，避免沿用上一张的比例
  useEffect(() => {
    setRatio(getCachedAspectRatio(uri) ?? fallback)
  }, [uri, fallback])

  const onLoad = useCallback(
    (event: LoadEvent) => {
      const width = event?.source?.width ?? 0
      const height = event?.source?.height ?? 0
      const next = ratioFromSize(width, height)
      if (!next) return
      cacheAspectRatio(uri, next)
      setRatio(next)
    },
    [uri],
  )

  return { ratio, onLoad }
}
