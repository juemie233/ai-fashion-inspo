/**
 * 采集源配置（`useScraperConfig`）的测试。
 *
 * Cookie 是采集登录态的唯一凭据，四个分支对应四种真实处境，提示文案不能混：
 * - `valid` 成功 / `invalid` 失效（带后端 detail）/ `no_file` 尚未导入 / 其它未知态
 *   （探测接口超时等，要提示「无法确认」而不是误判失效）；
 * - 导入 Cookie 要解析 JSON 并透传平台的 cookies 结构，成功后关弹窗清输入；
 * - 导入/删除失败都要带后端 detail，且成功/失败返回值供父级决定是否刷新。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  delete: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/client', () => ({ default: { post: mocks.post, delete: mocks.delete } }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useScraperConfig } from '../useScraperConfig'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

async function flush() {
  for (let i = 0; i < 4; i += 1) await Promise.resolve()
}

describe('useScraperConfig', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.post.mockResolvedValue({ data: {} })
    mocks.delete.mockResolvedValue({ data: {} })
  })

  describe('verifyCookie', () => {
    it('valid：提示有效并返回状态，busy 复位', async () => {
      const pending = deferred<{ data: { state: string; detail: string } }>()
      mocks.post.mockReturnValue(pending.promise)
      const config = useScraperConfig()

      const promise = config.verifyCookie('xiaohongshu')
      expect(config.verifyingCookie.value).toBe('xiaohongshu')

      pending.resolve({ data: { state: 'valid', detail: '' } })
      await expect(promise).resolves.toBe('valid')

      expect(mocks.post).toHaveBeenCalledWith('/scraper/cookie-verify/xiaohongshu')
      expect(mocks.message.success).toHaveBeenCalledWith('Cookie 登录态有效')
      expect(config.verifyingCookie.value).toBeNull()
    })

    it('invalid：提示失效并带后端 detail', async () => {
      mocks.post.mockResolvedValue({ data: { state: 'invalid', detail: '账号已退出登录' } })
      const config = useScraperConfig()

      await expect(config.verifyCookie('douyin')).resolves.toBe('invalid')

      expect(mocks.message.error).toHaveBeenCalledWith('Cookie 已失效: 账号已退出登录')
    })

    it('no_file：提示尚未导入', async () => {
      mocks.post.mockResolvedValue({ data: { state: 'no_file', detail: '' } })
      const config = useScraperConfig()

      await expect(config.verifyCookie('xiaohongshu')).resolves.toBe('no_file')

      expect(mocks.message.warning).toHaveBeenCalledWith('尚未导入 Cookie')
    })

    it('未知态：提示无法确认（不能误判为失效）', async () => {
      mocks.post.mockResolvedValue({ data: { state: 'unknown', detail: '探测接口超时' } })
      const config = useScraperConfig()

      await expect(config.verifyCookie('xiaohongshu')).resolves.toBe('unknown')

      expect(mocks.message.warning).toHaveBeenCalledWith('无法确认登录态: 探测接口超时')
    })

    it('请求失败：透出后端 detail 并返回 null，busy 复位', async () => {
      mocks.post.mockRejectedValue({ response: { data: { detail: '后端 500' } } })
      const config = useScraperConfig()

      await expect(config.verifyCookie('xiaohongshu')).resolves.toBeNull()

      expect(mocks.message.error).toHaveBeenCalledWith('校验失败: 后端 500')
      expect(config.verifyingCookie.value).toBeNull()
    })

    it('请求失败且无 detail：回退错误自身 message', async () => {
      mocks.post.mockRejectedValue(new Error('Network Error'))
      const config = useScraperConfig()

      await config.verifyCookie('xiaohongshu')

      expect(mocks.message.error).toHaveBeenCalledWith('校验失败: Network Error')
    })
  })

  describe('importCookie', () => {
    it('成功：JSON 解析后透传 platform/cookies，关弹窗清输入并返回 true', async () => {
      mocks.post.mockResolvedValue({ data: { imported: 5 } })
      const config = useScraperConfig()
      config.cookiePlatform.value = 'douyin'
      config.cookieJsonInput.value = '[{"name":"sessionid","value":"abc"}]'
      config.showingCookieImport.value = true

      await expect(config.importCookie()).resolves.toBe(true)

      expect(mocks.post).toHaveBeenCalledWith('/scraper/cookie-import', {
        platform: 'douyin',
        cookies: [{ name: 'sessionid', value: 'abc' }],
      })
      expect(mocks.message.success).toHaveBeenCalledWith('Cookie 已导入（5 条）')
      expect(config.showingCookieImport.value).toBe(false)
      expect(config.cookieJsonInput.value).toBe('')
    })

    it('成功但后端没回报条数：提示不带括号数字', async () => {
      mocks.post.mockResolvedValue({ data: {} })
      const config = useScraperConfig()
      config.cookieJsonInput.value = '[]'

      await config.importCookie()

      expect(mocks.message.success).toHaveBeenCalledWith('Cookie 已导入')
    })

    it('JSON 非法：提示导入失败、保留输入与弹窗、返回 false', async () => {
      const config = useScraperConfig()
      config.cookieJsonInput.value = '{broken'
      config.showingCookieImport.value = true

      await expect(config.importCookie()).resolves.toBe(false)

      const msg = mocks.message.error.mock.calls[0]?.[0] as string
      expect(msg).toMatch(/^导入失败: /)
      expect(mocks.post).not.toHaveBeenCalled()
      expect(config.showingCookieImport.value).toBe(true)
      expect(config.cookieJsonInput.value).toBe('{broken')
    })

    it('后端拒绝：透出 detail 并返回 false', async () => {
      mocks.post.mockRejectedValue({ response: { data: { detail: '平台不支持' } } })
      const config = useScraperConfig()
      config.cookieJsonInput.value = '[]'

      await expect(config.importCookie()).resolves.toBe(false)

      expect(mocks.message.error).toHaveBeenCalledWith('导入失败: 平台不支持')
    })
  })

  describe('deleteCookie', () => {
    it('成功：DELETE 平台路径、提示并返回 true，busy 复位', async () => {
      const pending = deferred<{ data: unknown }>()
      mocks.delete.mockReturnValue(pending.promise)
      const config = useScraperConfig()

      const promise = config.deleteCookie('douyin')
      expect(config.deletingCookie.value).toBe('douyin')

      pending.resolve({ data: {} })
      await expect(promise).resolves.toBe(true)

      expect(mocks.delete).toHaveBeenCalledWith('/scraper/cookie/douyin')
      expect(mocks.message.success).toHaveBeenCalledWith('Cookie 已删除')
      expect(config.deletingCookie.value).toBeNull()
    })

    it('失败：透出 detail、返回 false、busy 复位', async () => {
      mocks.delete.mockRejectedValue({ response: { data: { detail: '文件被占用' } } })
      const config = useScraperConfig()

      await expect(config.deleteCookie('xiaohongshu')).resolves.toBe(false)

      expect(mocks.message.error).toHaveBeenCalledWith('删除失败: 文件被占用')
      expect(config.deletingCookie.value).toBeNull()
    })
  })

  it('反复校验不同平台时 busy 始终跟随最新一次调用', async () => {
    mocks.post
      .mockResolvedValueOnce({ data: { state: 'valid', detail: '' } })
      .mockResolvedValueOnce({ data: { state: 'valid', detail: '' } })
    const config = useScraperConfig()

    await config.verifyCookie('xiaohongshu')
    await config.verifyCookie('douyin')

    await flush()
    expect(config.verifyingCookie.value).toBeNull()
    expect(mocks.post).toHaveBeenCalledTimes(2)
  })
})
