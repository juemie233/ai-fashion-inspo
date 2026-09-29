/**
 * useWorksDownload 测试：缺主页链接时「先解析、再下载」的编排。
 *
 * 关注四件事：① 没链接时先调解析接口，成功后用**解析出来的**链接提交下载；
 * ② 解析失败要给出后端原因且**不提交下载**；③ 已有链接时不该多调解析接口；
 * ④ f2 通道未就绪（profiles_available=false）时按钮禁用并显示后端原因。
 *
 * 这个编排值得单测的原因：它是两步且第二步依赖第一步，而所在页面用 Arco 卡片 +
 * 瀑布流，测试环境挂不出来（同 utils/__tests__/bloggerWorks.test.ts 的取舍）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import { ref } from 'vue'

import { useWorksDownload, type WorksDownloadSubjectWithId } from '../useWorksDownload'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))

vi.mock('@/api/client', () => ({ default: mocks }))

const DOUYIN_URL = 'https://www.douyin.com/user/MS4wLjABAAAAtang'

/** f2 状态：默认「通道就绪」；`profiles_available=false` 用于验证禁用态 */
function f2Status(overrides: Record<string, unknown> = {}) {
  return {
    available: true,
    reason: '可增量下载 21 个已登记博主的新作品',
    authors: 21,
    unknown_authors: [],
    f2_dir: 'C:/f2',
    root: 'C:/f2/Download/douyin/post',
    like_root: 'C:/f2/Download/douyin/like',
    like_user: '',
    like_available: true,
    like_reason: '',
    auto: {
      enabled: false,
      mode: 'post',
      interval_hours: 24,
      skip_live: false,
      available: true,
      reason: '',
      like_available: true,
      like_reason: '',
      collection_unsupported: false,
      authors: 21,
      last_task_at: null,
      next_due_at: null,
      running_task_id: null,
      running: null,
    },
    profiles_available: true,
    profiles_reason: '可点名下载指定博主的全部作品（首次翻全量，之后增量）',
    ...overrides,
  }
}

/** 「解析抖音主页」接口成功返回 */
function resolveResult(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    blogger_id: 1,
    name: '1123木头人',
    profile_url: DOUYIN_URL,
    platform_user_id: 'MS4wLjABAAAAtang',
    ip_location: '福建',
    source: 'f2_aweme',
    reason: '',
    ...overrides,
  }
}

/** 按 URL 分流：解析接口走 resolveResult，f2 任务接口返回 task_id */
function mockApi(resolve: unknown) {
  mocks.post.mockImplementation((url: string) => {
    if (url.includes('/resolve-douyin-profile')) return Promise.resolve({ data: resolve })
    return Promise.resolve({ data: { task_id: 7, message: '已创建任务' } })
  })
}

function f2ImportCall() {
  const call = mocks.post.mock.calls.find((c) => String(c[0]).includes('/scraper/f2-import'))
  return call as [string, unknown, { params: Record<string, string> }] | undefined
}

describe('useWorksDownload', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.post.mockReset()
    mocks.put.mockReset()
    mocks.get.mockResolvedValue({ data: f2Status() })
  })

  it('没有主页链接时按钮可点，但标记为「需要先解析」', async () => {
    const person = ref<WorksDownloadSubjectWithId>({ id: 1, platform: 'douyin', profile_url: null })
    const { state, loadStatus } = useWorksDownload(() => person.value)
    await loadStatus({ silent: true })

    expect(state.value).toEqual({ disabled: false, reason: '', needsResolve: true })
  })

  it('已有主页链接时不需要解析', async () => {
    const person = ref<WorksDownloadSubjectWithId>({
      id: 1,
      platform: 'douyin',
      profile_url: DOUYIN_URL,
    })
    const { state, loadStatus } = useWorksDownload(() => person.value)
    await loadStatus({ silent: true })

    expect(state.value).toEqual({ disabled: false, reason: '', needsResolve: false })
  })

  it('f2 通道未就绪 → 禁用并显示后端原因（缺链接也不例外）', async () => {
    mocks.get.mockResolvedValue({
      data: f2Status({
        profiles_available: false,
        profiles_reason: '未检测到 f2（python -m f2 不可用）',
      }),
    })
    const person = ref<WorksDownloadSubjectWithId>({ id: 1, platform: 'douyin', profile_url: null })
    const { state, loadStatus } = useWorksDownload(() => person.value)
    await loadStatus({ silent: true })

    expect(state.value.disabled).toBe(true)
    expect(state.value.reason).toContain('未检测到 f2')
    expect(state.value.needsResolve).toBe(false)
  })

  it('非抖音博主（模特/小红书）按钮禁用', () => {
    const person = ref<WorksDownloadSubjectWithId>({
      id: 1,
      platform: 'xiaohongshu',
      profile_url: 'https://www.xiaohongshu.com/x',
    })
    const { state } = useWorksDownload(() => person.value)

    expect(state.value.disabled).toBe(true)
    expect(state.value.reason).toContain('仅支持抖音')
  })

  it('缺链接：先解析，成功后用解析出的链接提交「按博主全量下载」', async () => {
    mockApi(resolveResult())
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const person = ref<WorksDownloadSubjectWithId>({ id: 1, platform: 'douyin', profile_url: null })
    let reloaded = 0
    const { downloadAllWorks, resolving } = useWorksDownload(
      () => person.value,
      () => {
        reloaded += 1
      },
    )

    const pending = downloadAllWorks()
    expect(resolving.value).toBe(true) // 转圈期间按钮不可重复点
    await pending

    expect(resolving.value).toBe(false)
    expect(mocks.post.mock.calls[0][0]).toBe('/bloggers/1/resolve-douyin-profile')
    const f2Call = f2ImportCall()
    expect(f2Call?.[2].params.profiles).toBe(DOUYIN_URL)
    expect(f2Call?.[2].params.mode).toBe('post')
    expect(reloaded).toBe(1) // 通知详情页重拉，让主页链接显示出来
    expect(success).toHaveBeenCalled()
  })

  it('解析失败：提示后端原因，且**不提交**下载', async () => {
    mockApi(
      resolveResult({
        ok: false,
        profile_url: '',
        reason: '这位博主的素材里没有可用的抖音作品 ID',
      }),
    )
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const person = ref<WorksDownloadSubjectWithId>({ id: 2, platform: 'douyin', profile_url: '' })
    const { downloadAllWorks } = useWorksDownload(() => person.value)

    await downloadAllWorks()

    expect(String(warn.mock.calls[0][0])).toContain('没有可用的抖音作品 ID')
    expect(f2ImportCall()).toBeUndefined()
  })

  it('解析接口抛错：提示错误且不提交下载', async () => {
    mocks.post.mockRejectedValue(new Error('网络超时'))
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const person = ref<WorksDownloadSubjectWithId>({ id: 3, platform: 'douyin', profile_url: null })
    const { downloadAllWorks, resolving } = useWorksDownload(() => person.value)

    await downloadAllWorks()

    expect(String(error.mock.calls[0][0])).toContain('网络超时')
    expect(resolving.value).toBe(false) // 失败也要复位，否则按钮永久转圈
    expect(f2ImportCall()).toBeUndefined()
  })

  it('已有链接：不调解析接口，直接提交下载', async () => {
    mockApi(resolveResult())
    const person = ref<WorksDownloadSubjectWithId>({
      id: 4,
      platform: 'douyin',
      profile_url: `  ${DOUYIN_URL}  `,
    })
    const { downloadAllWorks } = useWorksDownload(() => person.value)

    await downloadAllWorks()

    expect(mocks.post.mock.calls.some((c) => String(c[0]).includes('resolve-douyin-profile'))).toBe(
      false,
    )
    expect(f2ImportCall()?.[2].params.profiles).toBe(DOUYIN_URL)
  })
})
