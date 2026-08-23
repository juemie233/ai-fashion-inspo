/**
 * 轮询 seam：统一「定时执行回调」的定时器生命周期，替代各处重复的
 * setInterval/setTimeout + onBeforeUnmount 清理骨架。
 *
 * 这是 deep module：interface 只有 start/stop/running，内部封装定时器句柄、
 * 卸载清理、重复 start 幂等、立即执行一次等细节。新增需要轮询的 composable
 * 只需提供「做什么」(callback) 与「多久做一次」(intervalMs)，不再各自管理
 * timer 变量与清理逻辑——定时器泄漏/卸载后 setState 类问题修一次即处处受益。
 *
 * 设计取舍：
 * - 仅覆盖「固定/自适应间隔的重复轮询」这一真实 seam（任务中心、采集任务、
 *   GPU 监控、分析队列）。不内建退避/重试/代际号——那类状态机式轮询
 *   （useAdminTask）语义不同，强行统一会让 interface 膨胀，保持独立。
 * - intervalMs 支持传数字或返回数字的 getter，便于按活动态切换间隔
 *   （如分析队列 3s/15s 自适应），无需引入额外配置项。
 */

import { getCurrentInstance, onBeforeUnmount, readonly, ref } from 'vue'

interface UsePollingOptions {
  /** 轮询间隔（毫秒），或返回当前间隔的 getter（用于活动/空闲自适应） */
  intervalMs: number | (() => number)
  /** 每次触发执行的回调（可为异步；不 await，不捕获其异常） */
  callback: () => void | Promise<void>
  /** start 时是否立即执行一次 callback（默认 true） */
  immediate?: boolean
  /** 组件卸载时是否自动停止（默认 true） */
  autoCleanup?: boolean
}

export function usePolling(options: UsePollingOptions) {
  const { intervalMs, callback, immediate = true, autoCleanup = true } = options

  const running = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  function resolveInterval(): number {
    return typeof intervalMs === 'function' ? intervalMs() : intervalMs
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    running.value = false
  }

  function start() {
    // 幂等：已在轮询则不重建定时器（间隔变更通过 stop()+start() 生效）
    if (timer) return
    running.value = true
    if (immediate) {
      // 不 await：与既有实现一致，轮询回调失败由调用方自行处理
      void callback()
    }
    timer = setInterval(() => void callback(), resolveInterval())
  }

  // 仅在组件 setup 内注册卸载清理；在组件外（如单元测试直接调用）无实例可挂，
  // getCurrentInstance 为 null，跳过以避免 Vue 的生命周期告警。
  if (autoCleanup && getCurrentInstance()) {
    onBeforeUnmount(stop)
  }

  return { start, stop, running: readonly(running) }
}
