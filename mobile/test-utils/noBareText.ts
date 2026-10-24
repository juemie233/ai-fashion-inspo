/** 渲染树检查工具：找出「没包在 <Text> 里的裸字符串」。
 *
 * 为什么需要自建：react-test-renderer **不会**复现 React Native 的这条原生校验
 * （"Text strings must be rendered within a <Text> component."），普通渲染测试
 * 即使写了也抓不到。真机上它会让整棵组件树卸载 → 用户看到白屏
 * （2026-10-24 真实事故：灵感库卡片在刷新后触发）。
 *
 * 做法：把 toJSON() 的宿主树走一遍，父节点类型不是 Text 却挂了字符串/数字 → 报错。
 */

export interface BareTextHit {
  /** 父节点类型（如 View / TouchableOpacity 渲染出的宿主节点） */
  parentType: string
  /** 违规内容 */
  text: string
  /** 从根到该节点的路径，便于定位 */
  path: string
}

interface HostNode {
  type?: string
  props?: Record<string, unknown>
  children?: (HostNode | string | number | null)[]
}

const TEXT_TYPES = new Set(['Text', 'RCTText', 'RCTVirtualText'])

/** 收集所有违规的裸文本 */
export function findBareText(node: unknown, path = 'root'): BareTextHit[] {
  const hits: BareTextHit[] = []

  function walk(current: unknown, currentPath: string, parentType: string): void {
    if (current === null || current === undefined) return
    if (typeof current === 'string' || typeof current === 'number') {
      const text = String(current)
      if (text.trim() !== '' && !TEXT_TYPES.has(parentType)) {
        hits.push({ parentType, text: text.slice(0, 80), path: currentPath })
      }
      return
    }
    if (Array.isArray(current)) {
      current.forEach((c, i) => walk(c, `${currentPath}[${i}]`, parentType))
      return
    }
    if (typeof current !== 'object') return

    const host = current as HostNode
    const type = typeof host.type === 'string' ? host.type : 'Unknown'
    // 文本节点的 children 归父节点类型判断，这里继续下钻时把当前类型传下去
    const kids = (host.children ?? []) as unknown[]
    kids.forEach((c, i) => walk(c, `${currentPath}/${type}[${i}]`, type))
  }

  walk(node, path, 'Root')
  return hits
}
