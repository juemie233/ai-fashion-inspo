/**
 * useF2Import 测试：一键获取素材的可用性检查与任务提交。
 *
 * 关注两件事：① 提交参数要按界面选项透传（后端据此决定是否下载/收窄范围）；
 * ② 后端返回 task_id 为 null（环境不可用）时给出提示且不误报成功。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useF2Import } from '../useF2Import'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const STATUS = {
  available: true,
  reason: '可增量下载 21 个已采集作者的新作品',
  authors: 21,
  f2_dir: 'C:/f2',
  root: 'C:/f2/Download/douyin/post',
}

describe('useF2Import', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loadStatus 读取可用性状态', async () => {
    mocks.get.mockResolvedValue({ data: STATUS })
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

  it('submit 请求异常时返回 null 并提示错误', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.post.mockRejectedValue({ response: { data: { detail: '任务创建失败' } } })
    const { submit } = useF2Import()

    const taskId = await submit({ fetch: false })

    expect(taskId).toBeNull()
    expect(error).toHaveBeenCalledWith('任务创建失败')
    error.mockRestore()
  })
})
