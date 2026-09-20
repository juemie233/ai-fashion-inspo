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
  running: null,
}

const STATUS = {
  available: true,
  reason: '可增量下载 21 个已登记博主的新作品',
  authors: 21,
  unknown_authors: ['网易第五人格'],
  f2_dir: 'C:/f2',
  root: 'C:/f2/Download/douyin/post',
  like_root: 'C:/f2/Download/douyin/like',
  like_user: '',
  like_available: false,
  like_reason: '未配置「我的主页链接」',
  fetch_since_days: 14,
  auto: AUTO,
}

/** 每次返回全新对象：composable 会就地更新 status.auto，共享常量会串味到下一个用例 */
function makeStatus(auto: Partial<F2AutoStatus> = {}, extra: Partial<typeof STATUS> = {}) {
  return { ...STATUS, ...extra, auto: { ...AUTO, ...auto } }
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
    // 默认日期窗口来自后端配置（卡片据此填初值，不硬编码 14）
    expect(status.value?.fetch_since_days).toBe(14)
    // f2 用户库里未登记到博主的账号：卡片据此提示默认会跳过
    expect(status.value?.unknown_authors).toEqual(['网易第五人格'])
  })

  it('submit 透传「包含未登记账号」开关', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 43, message: '已提交' } })
    const { submit } = useF2Import()

    await submit({ fetch: true, include_unknown_authors: true })

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: { fetch: true, include_unknown_authors: true },
    })
    success.mockRestore()
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

  it('loadAuthors 读取博主清单（白名单 + 未登记账号 + 各自素材数）', async () => {
    const overview = {
      available: true,
      f2_dir: 'C:/f2',
      filter_active: true,
      registered_count: 1,
      unknown_count: 1,
      note: '另有 1 个 f2 账号未登记到博主库，默认会被跳过',
      registered: [
        {
          nickname: '里香1√',
          sec_user_id: 'sec-lixiang',
          aweme_count: 171,
          materials: 512,
          blogger_id: 304,
          blogger_name: '里香',
          profile_url: 'https://www.douyin.com/user/sec-lixiang',
        },
      ],
      unknown: [
        {
          nickname: '网易第五人格',
          sec_user_id: 'sec-wy',
          aweme_count: 42,
          materials: 142,
          blogger_id: null,
          blogger_name: null,
          profile_url: 'https://www.douyin.com/user/sec-wy',
        },
      ],
    }
    mocks.get.mockResolvedValue({ data: overview })
    const { f2Authors, authorsLoading, loadAuthors } = useF2Import()

    await loadAuthors()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-authors')
    expect(authorsLoading.value).toBe(false)
    expect(f2Authors.value?.registered[0].materials).toBe(512)
    expect(f2Authors.value?.registered[0].profile_url).toContain('/user/sec-lixiang')
    expect(f2Authors.value?.unknown[0].blogger_id).toBeNull()
  })

  it('loadAuthors 失败时置空并提示（清单不影响主流程）', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { f2Authors, loadAuthors } = useF2Import()

    await loadAuthors()

    expect(f2Authors.value).toBeNull()
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
    expect(status.value?.auto.running).toBeNull()
  })

  it('loadStatus 解析进行中任务的阶段与计数（卡片据此说明在干什么）', async () => {
    mocks.get.mockResolvedValue({
      data: makeStatus({
        running_task_id: 329,
        running: {
          id: 329,
          status: 'running',
          progress: 7,
          done: 4,
          total: 21,
          stage: 'download',
          fetch_mode: 'post',
          like_progress: null,
        },
      }),
    })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value?.auto.running_task_id).toBe(329)
    expect(status.value?.auto.running?.stage).toBe('download')
    expect(status.value?.auto.running?.done).toBe(4)
  })

  it('loadStatus 解析「我的喜欢」的实时下载统计（点赞总数未知，靠它判断在下载）', async () => {
    mocks.get.mockResolvedValue({
      data: makeStatus({
        running_task_id: 341,
        running: {
          id: 341,
          status: 'running',
          progress: 3,
          done: 0,
          total: 0,
          stage: 'download',
          fetch_mode: 'like',
          like_progress: { files: 3517, bytes: 2168000000, added: 3163, added_bytes: 2000000 },
        },
      }),
    })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value?.auto.running?.fetch_mode).toBe('like')
    expect(status.value?.auto.running?.like_progress?.added).toBe(3163)
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

  it('submit 透传「我的喜欢」模式与主页链接', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 51, message: '已提交' } })
    const { submit } = useF2Import()

    await submit({ fetch: true, mode: 'like', like_user: 'MS4wLjABAAAAme' })

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', null, {
      params: { fetch: true, mode: 'like', like_user: 'MS4wLjABAAAAme' },
    })
    success.mockRestore()
  })

  it('setLikeUser 保存主页链接并回读可用性（点赞入口据此放行）', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValueOnce({ data: makeStatus() })
    mocks.put.mockResolvedValue({
      data: {
        message: '已保存「我的主页链接」',
        like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
      },
    })
    const { status, loadStatus, setLikeUser, likeUserSaving } = useF2Import()
    await loadStatus()

    // 保存后回读：后端此时已能采集我的喜欢
    mocks.get.mockResolvedValueOnce({
      data: makeStatus(
        {},
        {
          like_user: 'https://www.douyin.com/user/MS4wLjABAAAAme',
          like_available: true,
          like_reason: '已配置「我的主页链接」，可采集我的喜欢（点赞作品）',
        },
      ),
    })
    const ok = await setLikeUser('MS4wLjABAAAAme')

    expect(ok).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-like-user', null, {
      params: { like_user: 'MS4wLjABAAAAme', persist: true },
    })
    expect(status.value?.like_user).toBe('https://www.douyin.com/user/MS4wLjABAAAAme')
    expect(status.value?.like_available).toBe(true)
    expect(likeUserSaving.value).toBe(false)
    success.mockRestore()
  })
})
