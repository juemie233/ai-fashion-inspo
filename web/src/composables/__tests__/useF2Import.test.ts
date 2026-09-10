/**
 * useF2Import 测试：一键获取素材的可用性检查与任务提交。
 *
 * 关注两件事：① 提交参数要按界面选项透传（后端据此决定是否下载/收窄范围）；
 * ② 后端返回 task_id 为 null（环境不可用）时给出提示且不误报成功。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useF2Import, type F2AutoStatus } from '../useF2Import'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const AUTO: F2AutoStatus = {
  enabled: false,
  interval_hours: 24,
  skip_live: false,
  available: true,
  reason: '可增量下载 21 个已采集作者的新作品',
  authors: 21,
  last_task_at: null,
  next_due_at: null,
  running_task_id: null,
}

const STATUS = {
  available: true,
  reason: '可增量下载 21 个已采集作者的新作品',
  authors: 21,
  f2_dir: 'C:/f2',
  root: 'C:/f2/Download/douyin/post',
  auto: AUTO,
}

/** 每次返回全新对象：composable 会就地更新 status.auto，共享常量会串味到下一个用例 */
function makeStatus(auto: Partial<F2AutoStatus> = {}) {
  return { ...STATUS, auto: { ...AUTO, ...auto } }
}

describe('useF2Import', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loadStatus 读取可用性状态', async () => {
    mocks.get.mockResolvedValue({ data: makeStatus() })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-status')
    expect(status.value?.available).toBe(true)
    expect(status.value?.authors).toBe(21)
  })

  it('loadStatus 失败时置空并提示（不抛异常）', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value).toBeNull()
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('submit 透传选项并返回 task_id', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 42, message: '已提交' } })
    const { submit, submitting } = useF2Import()

    const taskId = await submit({ fetch: true, authors: '里香', limit: 100, skip_live: true })

    expect(taskId).toBe(42)
    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: { fetch: true, authors: '里香', limit: 100, skip_live: true },
    })
    expect(submitting.value).toBe(false)
    expect(success).toHaveBeenCalled()
    success.mockRestore()
  })

  it('submit 在后端未创建任务时提示原因、不误报成功', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({
      data: { task_id: null, message: '未检测到 f2（python -m f2 不可用）' },
    })
    const { submit } = useF2Import()

    const taskId = await submit({ fetch: true })

    expect(taskId).toBeNull()
    expect(warn).toHaveBeenCalledWith('未检测到 f2（python -m f2 不可用）')
    expect(success).not.toHaveBeenCalled()
    warn.mockRestore()
    success.mockRestore()
  })

  it('submit 复用进行中的任务时给出提示而非「已提交」', async () => {
    const info = vi.spyOn(Message, 'info').mockImplementation((() => {}) as never)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({
      data: {
        task_id: 7,
        message: '已有进行中的一键获取素材任务（#7），请等待完成或先取消',
        reused: true,
      },
    })
    const { submit } = useF2Import()

    const taskId = await submit({ fetch: true })

    expect(taskId).toBe(7)
    expect(info).toHaveBeenCalled()
    expect(success).not.toHaveBeenCalled()
    info.mockRestore()
    success.mockRestore()
  })

  it('submit 请求异常时返回 null 并提示错误', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.post.mockRejectedValue({ response: { data: { detail: '任务创建失败' } } })
    const { submit } = useF2Import()

    const taskId = await submit({ fetch: false })

    expect(taskId).toBeNull()
    expect(error).toHaveBeenCalledWith('任务创建失败')
    error.mockRestore()
  })

  it('loadStatus 解析每日自动获取配置', async () => {
    mocks.get.mockResolvedValue({
      data: makeStatus({
        enabled: true,
        interval_hours: 12,
        last_task_at: '2026-09-10T04:00:00Z',
        next_due_at: '2026-09-10T16:00:00Z',
      }),
    })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value?.auto.enabled).toBe(true)
    expect(status.value?.auto.interval_hours).toBe(12)
    expect(status.value?.auto.next_due_at).toBe('2026-09-10T16:00:00Z')
  })

  it('setAuto 透传开关与间隔，并用回包更新本地状态', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue({ data: makeStatus() })
    mocks.put.mockResolvedValue({
      data: {
        message: '每日自动获取素材已开启（间隔 12 小时）',
        auto: { ...AUTO, enabled: true, interval_hours: 12 },
      },
    })
    const { status, loadStatus, setAuto, autoSaving } = useF2Import()
    await loadStatus()

    const ok = await setAuto(true, 12)

    expect(ok).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-auto', null, {
      params: { enabled: true, interval_hours: 12, persist: true },
    })
    expect(status.value?.auto.enabled).toBe(true)
    expect(status.value?.auto.interval_hours).toBe(12)
    expect(autoSaving.value).toBe(false)
    success.mockRestore()
  })

  it('setAuto 失败时回读状态并返回 false（避免开关停在错误位置）', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue({ data: makeStatus() })
    mocks.put.mockRejectedValue({ response: { data: { detail: '写入 .env 失败' } } })
    const { status, loadStatus, setAuto } = useF2Import()
    await loadStatus()

    const ok = await setAuto(true)

    expect(ok).toBe(false)
    expect(error).toHaveBeenCalledWith('写入 .env 失败')
    // 回读后开关仍是后端的真实状态（关闭）
    expect(status.value?.auto.enabled).toBe(false)
    expect(mocks.get).toHaveBeenCalledTimes(2)
    error.mockRestore()
  })
})
