/** 图片真实宽高比的缓存。
 *
 * 审查报告 P0-8：详情页把所有图片塞进固定 4:5 容器 + `contain`，横图上下各空一大块；
 * §7.3 明确**不改后端契约**（不补 width/height 字段），所以比例在客户端解决：
 * 图片 `onLoad` 事件自带 `source.width/height`，拿到后写进本缓存，
 * 同一张图在列表/详情/相似推荐里复用（只探测一次）。
 *
 * 注意：这里**不用** RN 的 `Image.getSize`——RN 0.76 已把它改成 Promise 实现，
 * 而 jest-expo 的原生桩仍是回调式，测试里直接抛 TypeError（已实测）。
 * 走渲染后的 `onLoad` 既不额外发请求，也不依赖原生桩。
 */

/** 比例下限（极窄的竖图，避免占满整屏） */
export const MIN_RATIO = 0.4
/** 比例上限（极宽的横图） */
export const MAX_RATIO = 2.5

/** uri → 宽高比（w / h） */
const cache = new Map<string, number>()

/** 把比例钳制到 [MIN_RATIO, MAX_RATIO] */
export function clampRatio(ratio: number): number {
  if (!Number.isFinite(ratio) || ratio <= 0) return 0.75
  return Math.min(MAX_RATIO, Math.max(MIN_RATIO, ratio))
}

/** 由宽高算比例（非法尺寸返回 null） */
export function ratioFromSize(width: number, height: number): number | null {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null
  return clampRatio(width / height)
}

/** 读缓存（同步，用于首帧避免闪跳） */
export function getCachedAspectRatio(uri: string | null | undefined): number | undefined {
  if (!uri) return undefined
  return cache.get(uri)
}

/** 写缓存（`onLoad` 回调与测试都走这里） */
export function cacheAspectRatio(uri: string | null | undefined, ratio: number): void {
  if (!uri || !Number.isFinite(ratio) || ratio <= 0) return
  cache.set(uri, clampRatio(ratio))
}

/** 清空缓存（测试用） */
export function clearAspectRatioCache(): void {
  cache.clear()
}
