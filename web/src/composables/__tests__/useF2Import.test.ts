/**
 * useF2Import 测试：一键获取素材的可用性检查与任务提交。
 *
 * 关注两件事：① 提交参数要按界面选项透传（后端据此决定是否下载/收窄范围）；
 * ② 后端返回 task_id 为 null（环境不可用）时给出提示且不误报成功。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { useF2Import, __resetF2ImportStateForTests, type F2AutoStatus } from '../useF2Import'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/api/client', () => ({ default: mocks }))

const AUTO: F2AutoStatus = {
  enabled: false,
  mode: 'post',
  interval_hours: 24,
  skip_live: false,
  available: true,
  reason: '可增量下载 21 个已采集作者的新作品',
  like_available: false,
  like_reason: '未配置「我的主页链接」',
  collection_unsupported: false,
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
    __resetF2ImportStateForTests()
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

  it('多个 useF2Import 实例共享同一份 status', async () => {
    mocks.get.mockResolvedValue({ data: makeStatus() })
    const a = useF2Import()
    const b = useF2Import()

    // 同一模块级 ref，而不是各实例各自一份
    expect(a.status).toBe(b.status)
    await a.loadStatus()
    expect(b.status.value?.authors).toBe(21)
  })

  it('并发 loadStatus：旧响应不能覆盖新响应', async () => {
    let resolveOld!: (value: unknown) => void
    let resolveNew!: (value: unknown) => void
    mocks.get
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveNew = resolve
          }),
      )
    const a = useF2Import()
    const b = useF2Import()

    const oldRequest = a.loadStatus()
    const newRequest = b.loadStatus()
    resolveNew({ data: makeStatus({}, { authors: 99 }) })
    await newRequest
    resolveOld({ data: makeStatus({}, { authors: 1 }) })
    await oldRequest

    expect(a.status.value?.authors).toBe(99)
  })

  it('状态响应关键字段缺失/类型异常：按 false 与空串兜底，不崩', async () => {
    mocks.get.mockResolvedValue({
      data: {
        available: 'yes',
        reason: 123,
        like_available: null,
        collect_available: undefined,
      },
    })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value).toMatchObject({
      available: false,
      reason: '',
      like_available: false,
      like_reason: '',
      collect_available: false,
      collect_reason: '',
      authors: 0,
      unknown_authors: [],
    })
    // profiles_* 不做缺失兜底：旧后端没有该字段时保持 undefined（卡片按 !== false 兼容）
    expect(status.value?.profiles_available).toBeUndefined()
  })

  it('已成功加载后再次 loadStatus 失败：保留旧数据并提示', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValueOnce({ data: makeStatus() }).mockRejectedValueOnce(new Error('boom'))
    const { status, loadStatus } = useF2Import()
    await loadStatus()
    const previous = status.value

    await loadStatus()

    expect(status.value).toBe(previous)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('submit 透传「包含未登记账号」开关', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 43, message: '已提交' } })
    const { submit } = useF2Import()

    await submit({ fetch: true, include_unknown_authors: true })

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', {
      fetch: true,
      include_unknown_authors: true,
    })
    success.mockRestore()
  })

  it('静默轮询失败不清空状态、不弹 toast，保留上一次好数据', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValueOnce({ data: makeStatus() }).mockRejectedValueOnce(new Error('boom'))
    const { status, loadStatus } = useF2Import()

    await loadStatus()
    const before = status.value
    await loadStatus({ silent: true })

    expect(status.value).toBe(before)
    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
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
    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', {
      fetch: true,
      authors: '里香',
      limit: 100,
      skip_live: true,
    })
    expect(submitting.value).toBe(false)
    expect(success).toHaveBeenCalled()
    success.mockRestore()
  })

  it('preview 只把「与扫描相关」的选项发给预览接口（不传 fetch/since_days）', async () => {
    mocks.get.mockResolvedValue({
      data: {
        root: 'E:/f2/post',
        files: 10,
        files_to_import: 7,
        works: 3,
        skipped: { 内容已在库: 3 },
        deferred_works: 0,
        authors_scope: ['里香'],
        has_any: true,
      },
    })
    const { preview, previewing } = useF2Import()

    const data = await preview({
      fetch: true,
      authors: '里香',
      limit: 50,
      skip_live: true,
      include_unknown_authors: true,
    })

    expect(data?.files_to_import).toBe(7)
    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-preview', {
      params: {
        authors: '里香',
        limit: 50,
        skip_live: true,
        mode: 'post',
        include_unknown_authors: true,
      },
    })
    // 预览是同步等待结果的（界面要弹窗），结束后必须复位 loading
    expect(previewing.value).toBe(false)
  })

  it('preview 失败时返回 null 并提示（不把错误当空结果）', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { preview } = useF2Import()

    expect(await preview({ fetch: false })).toBeNull()
    expect(error).toHaveBeenCalled()
    error.mockRestore()
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

  it('submitDetailed 复用进行中的任务时返回 reused，submit 保持旧签名返回 task_id', async () => {
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({
      data: {
        task_id: 7,
        message: '已有进行中的一键获取素材任务（#7），请等待完成或先取消',
        reused: true,
      },
    })
    const { submit, submitDetailed } = useF2Import()

    const detail = await submitDetailed({ fetch: true })
    expect(detail).toEqual({ taskId: 7, reused: true })
    expect(warn).toHaveBeenCalled()
    expect(success).not.toHaveBeenCalled()

    const taskId = await submit({ fetch: true })
    expect(taskId).toBe(7)
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
          download_progress: null,
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
          download_progress: { files: 3517, bytes: 2168000000, added: 3163, added_bytes: 2000000 },
        },
      }),
    })
    const { status, loadStatus } = useF2Import()

    await loadStatus()

    expect(status.value?.auto.running?.fetch_mode).toBe('like')
    expect(status.value?.auto.running?.download_progress?.added).toBe(3163)
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

    const ok = await setAuto({ enabled: true, intervalHours: 12 })

    expect(ok).toBe(true)
    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-auto', null, {
      params: { enabled: true, interval_hours: 12, persist: true },
    })
    expect(status.value?.auto.enabled).toBe(true)
    expect(status.value?.auto.interval_hours).toBe(12)
    expect(autoSaving.value).toBe(false)
    success.mockRestore()
  })

  it('setAuto 显式指定入口时下发 mode（未指定则沿用后端已保存的模式）', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue({ data: makeStatus() })
    mocks.put.mockResolvedValue({
      data: { message: '已开启', auto: { ...AUTO, enabled: true, mode: 'collection' } },
    })
    const { loadStatus, setAuto } = useF2Import()
    await loadStatus()

    await setAuto({ enabled: true, intervalHours: 24, persist: true, mode: 'collection' })

    expect(mocks.put).toHaveBeenCalledWith('/scraper/f2-auto', null, {
      params: { enabled: true, interval_hours: 24, persist: true, mode: 'collection' },
    })
    success.mockRestore()
  })

  it('setAuto 失败时回读状态并返回 false（避免开关停在错误位置）', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockResolvedValue({ data: makeStatus() })
    mocks.put.mockRejectedValue({ response: { data: { detail: '写入 .env 失败' } } })
    const { status, loadStatus, setAuto } = useF2Import()
    await loadStatus()

    const ok = await setAuto({ enabled: true })

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

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', {
      fetch: true,
      mode: 'like',
      like_user: 'MS4wLjABAAAAme',
    })
    success.mockRestore()
  })

  it('submit 透传选中的收藏夹（先扫描、后下载：只下勾选的夹）', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 52, message: '已提交' } })
    const { submit } = useF2Import()

    await submit({
      fetch: true,
      mode: 'collection',
      like_user: 'MS4wLjABAAAAme',
      collect_ids: ['111', '333'],
    })

    expect(mocks.post).toHaveBeenCalledWith('/scraper/f2-import', {
      fetch: true,
      mode: 'collection',
      like_user: 'MS4wLjABAAAAme',
      // JSON body 原生数组：不再 join(',')；后端 F2ImportRequest 直接按数组解析
      collect_ids: ['111', '333'],
    })
    // query 不再携带旧的逗号串参数（axios 第三参不传）
    expect(mocks.post.mock.calls[0]).toHaveLength(2)
    // 提示文案要说清是「按收藏夹下载」，否则用户以为又全量下了一遍收藏
    expect(success).toHaveBeenCalledWith(expect.stringContaining('按收藏夹下载（2 个夹）'))
    success.mockRestore()
  })

  it('submit 用 JSON body 传 profiles 原生数组，布尔/数字保持原值', async () => {
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    mocks.post.mockResolvedValue({ data: { task_id: 53, message: '已提交' } })
    const { submit } = useF2Import()

    await submit({
      fetch: true,
      profiles: ['https://www.douyin.com/user/MS4wLjABAAAAme', 'MS4wLjABAAAAnother'],
      limit: 30,
      skip_live: false,
      make_thumbnails: true,
      since_days: 0,
    })

    const [url, body, config] = mocks.post.mock.calls[0] as [
      string,
      Record<string, unknown>,
      unknown,
    ]
    expect(url).toBe('/scraper/f2-import')
    expect(config).toBeUndefined() // 不再传 params
    expect(Array.isArray(body.profiles)).toBe(true)
    expect(body.profiles).toEqual([
      'https://www.douyin.com/user/MS4wLjABAAAAme',
      'MS4wLjABAAAAnother',
    ])
    expect(body.limit).toBe(30)
    expect(body.skip_live).toBe(false)
    expect(body.make_thumbnails).toBe(true)
    expect(body.since_days).toBe(0)
    success.mockRestore()
  })

  it('scanCollects 读取收藏夹清单（夹名 / 件数）并缓存到 collectFolders', async () => {
    mocks.get.mockResolvedValue({
      data: {
        folders: [
          { id: '111', name: '秘书OL', total: 96 },
          { id: '222', name: '股票', total: 1 },
        ],
        total_folders: 2,
        total_works: 97,
        cookie_source: 'conf/app.yaml',
      },
    })
    const { scanCollects, collectFolders, collectScanning } = useF2Import()

    const data = await scanCollects()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/f2-collects')
    expect(data?.total_folders).toBe(2)
    expect(collectFolders.value?.folders[0].name).toBe('秘书OL')
    expect(collectScanning.value).toBe(false)
  })

  it('scanCollects 失败（Cookie 失效 / 风控）返回 null 并置空清单', async () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    mocks.get.mockRejectedValue(new Error('boom'))
    const { scanCollects, collectFolders } = useF2Import()

    expect(await scanCollects()).toBeNull()
    expect(collectFolders.value).toBeNull()
    expect(error).toHaveBeenCalled()
    error.mockRestore()
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
