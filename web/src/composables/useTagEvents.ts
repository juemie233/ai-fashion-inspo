/** 标签变更事件总线：标签被创建 / 更新 / 删除 / 合并 / 批量操作后广播，
 * 各视图按需订阅以刷新数据，替代此前通过 props/@saved/@changed 层层透传回调。
 *
 * 设计为模块级单例（emitter 在模块作用域），任意组件调用 useTagEvents()
 * 拿到的都是同一个总线。用 Vue 自定义事件 + 轻量发布订阅实现，无需额外依赖。
 */

import { getCurrentInstance, onBeforeUnmount } from 'vue'

/** 标签变更事件名 */
export type TagEventType = 'created' | 'updated' | 'deleted' | 'merged' | 'batch-edited' | 'reorder'

/** 事件载荷：合并时携带 source/target，其余携带受影响的标签 id 列表（可选） */
export interface TagEventPayload {
  type: TagEventType
  /** 受影响的标签 id（删除/更新/批量）；合并时为被合并（删除）的 source id */
  tagIds?: number[]
  /** 合并目标标签 id（仅 merged 事件） */
  targetId?: number
}

type Handler = (payload: TagEventPayload) => void

// ── 模块级单例发布订阅 ──
const handlers = new Set<Handler>()

function emit(payload: TagEventPayload) {
  // 复制一份，避免回调中注销导致迭代异常
  for (const h of [...handlers]) h(payload)
}

function on(handler: Handler) {
  handlers.add(handler)
}

function off(handler: Handler) {
  handlers.delete(handler)
}

export function useTagEvents() {
  /** 广播一次标签变更 */
  function notifyTagChanged(payload: TagEventPayload) {
    emit(payload)
  }

  /**
   * 订阅标签变更，组件卸载时自动注销。
   * @param handler 事件处理函数；可通过第二个参数过滤关心的事件类型
   */
  function onTagChanged(handler: Handler, types?: TagEventType[]) {
    const wrapped: Handler = (payload) => {
      if (!types || types.includes(payload.type)) handler(payload)
    }
    on(wrapped)
    // 仅在组件 setup 中调用时自动注销；在组件外（如单测）使用需手动 off
    if (getCurrentInstance()) {
      onBeforeUnmount(() => off(wrapped))
    }
    return () => off(wrapped)
  }

  return { notifyTagChanged, onTagChanged }
}
