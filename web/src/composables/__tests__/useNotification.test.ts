/**
 * 桌面通知（`useNotification`）的测试。
 *
 * 这段逻辑看着简单，但它决定「任务失败时用户能不能被叫醒」，接口窄、坑集中：
 *
 * 1. **浏览器不支持 / 权限被拒时必须静默**：不能抛错（否则整个轮询链断掉），
 *    也不能假装发出去了；
 * 2. **权限只请求一次**：`granted` 后不再弹框（重复弹框会被浏览器忽略，且很烦）；
 * 3. **失败率告警的阈值边界**：`total === 0` 不能除零；`rate >= threshold` 才算告警
 *    （等于阈值要告警——0.3 正好是「三成失败」这条线）。
 */

import { beforeEach, describe, expect, it, vi, type MockedFunction } from 'vitest'

describe('useNotification', () => {
  // 既是可调用替身、又要访问 .mock.calls：用 MockedFunction 类型
  let notifyCtor: MockedFunction<(title: string, options?: NotificationOptions) => void>

  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    notifyCtor = vi.fn<(title: string, options?: NotificationOptions) => void>()
    // 每个用例重新定义 window.Notification，确保模块级 permission 初值可控
    class FakeNotification {
      static permission = 'default'
      static requestPermission = vi.fn(async () => 'granted')
      constructor(title: string, options?: NotificationOptions) {
        notifyCtor(title, options)
      }
    }
    vi.stubGlobal('Notification', FakeNotification)
  })

  async function load() {
    const mod = await import('../useNotification')
    return mod.useNotification()
  }

  it('权限已授予时直接返回 true，不再弹框', async () => {
    const Fake = (
      globalThis as unknown as {
        Notification: { permission: string; requestPermission: ReturnType<typeof vi.fn> }
      }
    ).Notification
    Fake.permission = 'granted'
    const { requestPermission } = await load()

    expect(await requestPermission()).toBe(true)
    expect(Fake.requestPermission).not.toHaveBeenCalled()
  })

  it('权限为 default 时请求一次并缓存结果；被拒返回 false', async () => {
    const Fake = (
      globalThis as unknown as {
        Notification: { permission: string; requestPermission: ReturnType<typeof vi.fn> }
      }
    ).Notification
    Fake.permission = 'default'
    Fake.requestPermission.mockResolvedValue('denied')
    const { requestPermission, permission } = await load()

    expect(await requestPermission()).toBe(false)
    expect(permission.value).toBe('denied')

    // 再次请求：不会重复弹框（permission 已不是 granted，但仍会请求——
    // 这里锁定「浏览器返回 denied 后状态被记录」这一行为）
    Fake.requestPermission.mockResolvedValue('granted')
    expect(await requestPermission()).toBe(true)
    expect(permission.value).toBe('granted')
  })

  it('浏览器不支持 Notification 时静默降级（不抛错、不发通知）', async () => {
    vi.stubGlobal('Notification', undefined)
    // 模拟「没有 Notification 这个全局」：in 判断为 false
    delete (globalThis as Record<string, unknown>).Notification

    const { requestPermission, notify, requestAndNotify } = await load()

    await expect(requestPermission()).resolves.toBe(false)
    expect(() => notify('标题')).not.toThrow()
    await expect(requestAndNotify('标题')).resolves.toBeUndefined()
    expect(notifyCtor).not.toHaveBeenCalled()
  })

  it('权限未授予时 notify 静默跳过', async () => {
    const Fake = (globalThis as unknown as { Notification: { permission: string } }).Notification
    Fake.permission = 'default'
    const { notify } = await load()

    notify('任务失败')

    expect(notifyCtor).not.toHaveBeenCalled()
  })

  it('权限授予后 notify 带默认图标发出', async () => {
    const Fake = (globalThis as unknown as { Notification: { permission: string } }).Notification
    Fake.permission = 'granted'
    const { notify } = await load()

    notify('出图完成', { body: '共 4 张' })

    expect(notifyCtor).toHaveBeenCalledTimes(1)
    const [title, options] = notifyCtor.mock.calls[0] ?? []
    expect(title).toBe('出图完成')
    expect(options?.body).toBe('共 4 张')
    expect(options?.icon).toBe('/favicon.ico')
  })

  it('requestAndNotify 在授权后才会发通知', async () => {
    const Fake = (
      globalThis as unknown as {
        Notification: { permission: string; requestPermission: ReturnType<typeof vi.fn> }
      }
    ).Notification
    Fake.permission = 'default'
    Fake.requestPermission.mockResolvedValue('granted')
    const { requestAndNotify } = await load()

    await requestAndNotify('已授权')

    expect(notifyCtor).toHaveBeenCalledTimes(1)
  })

  it('失败率告警：低于阈值不发、达到阈值发、total 为 0 不除零', async () => {
    const Fake = (globalThis as unknown as { Notification: { permission: string } }).Notification
    Fake.permission = 'granted'
    const { checkFailureAlert } = await load()

    checkFailureAlert(0, 0) // 空队列：不能除零/不能告警
    expect(notifyCtor).not.toHaveBeenCalled()

    checkFailureAlert(29, 100) // 29% < 30%
    expect(notifyCtor).not.toHaveBeenCalled()

    checkFailureAlert(30, 100) // 恰好 30%：要告警（>= 阈值）
    expect(notifyCtor).toHaveBeenCalledTimes(1)
    const [title, options] = notifyCtor.mock.calls[0] ?? []
    expect(title).toContain('失败率告警')
    expect(options?.body).toContain('30%')
    expect(options?.tag).toBe('failure-alert')

    checkFailureAlert(5, 10, 0.9) // 自定义阈值 50% < 90%
    expect(notifyCtor).toHaveBeenCalledTimes(1)
  })
})
