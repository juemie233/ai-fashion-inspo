/** 任务轮询到终态的共用骨架（人脸扫描/匹配、聚合聚类两处共用）。 */

/** 可轮询任务的最小形状（后端任务都带 id 与 status） */
export interface PollableTask {
  id: number
  status: string
}

/**
 * 轮询任务直到终态：每 ``intervalMs`` 刷新一次状态，取到「不是该任务 / 已不在
 * running·pending」即调用 ``onIdle`` 收尾并返回。
 *
 * 为什么收敛到 utils：原先人脸页的扫描(3s)与聚类(2s)各写了一份几乎相同的
 * `while + setTimeout` 循环，差别只在「怎么刷新」与「收尾做什么」——
 * 那两点由调用方以回调提供，循环骨架只留一份。
 *
 * 与 ``usePolling`` 的分工：``usePolling`` 管「固定间隔重复执行 + 定时器生命周期」；
 * 本函数是「轮询到某个条件成立为止」的状态机式收尾，语义不同故独立存在。
 *
 * @param options.taskId 目标任务 id（用于判定「当前拿到的还是不是这个任务」）
 * @param options.intervalMs 轮询间隔（毫秒）
 * @param options.refresh 刷新一次状态，返回目标任务的最新快照；取不到返回 null
 * @param options.onIdle 任务到终态后的收尾（刷新结果区等）
 */
export async function pollTaskUntilIdle<T extends PollableTask>(options: {
  taskId: number
  intervalMs: number
  refresh: () => Promise<T | null>
  onIdle: () => void | Promise<void>
}): Promise<void> {
  // 有意不设最大轮询次数：任务可能长时间排队，收尾条件由任务状态本身决定
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, options.intervalMs))
    const current = await options.refresh()
    if (
      !current ||
      current.id !== options.taskId ||
      !['running', 'pending'].includes(current.status)
    ) {
      await options.onIdle()
      return
    }
  }
}
