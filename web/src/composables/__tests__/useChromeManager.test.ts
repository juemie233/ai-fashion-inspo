/**
 * 采集 Chrome 生命周期（useChromeManager）：状态刷新 / 启动 / 停止与提示分支。
 *
 * 该 composable 决定采集页顶部「Chrome 状态」徽标的文案与颜色，启动分支里有四种
 * 结局（running / port_conflict / starting / 其它）各自不同的 Message 提示，
 * 请求失败还要把后端 detail 拼进提示。文案或状态映射写错，用户看到的就是
 * 「启动失败」而不知道该关哪个进程，所以这里逐分支锁死。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'
import type { ChromeStatus } from '@/types/scraper'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/api/client', () => ({ default: { get: mocks.get, post: mocks.post } }))

import { CHROME_STATE_LABELS, CHROME_STATE_TAG, useChromeManager } from '../useChromeManager'

const msgMock = () => ({ close: () => {} })

function chromeStatus(over: Partial<ChromeStatus> = {}): ChromeStatus {
  return { state: 'running', detail: '', pid: 123, ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(Message, 'success').mockImplementation(msgMock)
  vi.spyOn(Message, 'error').mockImplementation(msgMock)
  vi.spyOn(Message, 'warning').mockImplementation(msgMock)
})

describe('CHROME_STATE_LABELS / CHROME_STATE_TAG', () => {
  it('四种状态都有中文文案与 Arco 语义色', () => {
    expect(CHROME_STATE_LABELS).toEqual({
      running: '已连接',
      not_started: '未启动',
      port_conflict: '端口冲突',
      starting: '启动中',
    })
    expect(CHROME_STATE_TAG).toEqual({
      running: 'success',
      not_started: 'default',
      port_conflict: 'error',
      starting: 'warning',
    })
  })
})

describe('refreshChromeStatus', () => {
  it('成功：把接口返回的状态写入 chromeStatus', async () => {
    const status = chromeStatus({ state: 'starting', detail: '正在拉起', pid: null })
    mocks.get.mockResolvedValueOnce({ data: status })
    const manager = useChromeManager()

    await manager.refreshChromeStatus()

    expect(mocks.get).toHaveBeenCalledWith('/scraper/chrome/status')
    expect(manager.chromeStatus.value).toEqual(status)
  })

  it('失败：置 null（徽标回退未知态），不抛错', async () => {
    mocks.get.mockRejectedValueOnce(new Error('network'))
    const manager = useChromeManager()
    manager.chromeStatus.value = chromeStatus()

    await expect(manager.refreshChromeStatus()).resolves.toBeUndefined()

    expect(manager.chromeStatus.value).toBeNull()
  })
})

describe('startChrome 启动分支', () => {
  it('running：写入状态并提示「Chrome 已启动」', async () => {
    const status = chromeStatus({ state: 'running' })
    mocks.post.mockResolvedValueOnce({ data: status })
    const manager = useChromeManager()

    await manager.startChrome()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/chrome/start')
    expect(manager.chromeStatus.value).toEqual(status)
    expect(Message.success).toHaveBeenCalledWith('Chrome 已启动')
    expect(Message.error).not.toHaveBeenCalled()
    expect(manager.chromeBusy.value).toBe(false)
  })

  it('port_conflict：优先展示后端 detail，便于用户定位占用进程', async () => {
    mocks.post.mockResolvedValueOnce({
      data: chromeStatus({ state: 'port_conflict', detail: '端口 9222 被占用', pid: null }),
    })
    const manager = useChromeManager()

    await manager.startChrome()

    expect(Message.error).toHaveBeenCalledWith('端口 9222 被占用')
  })

  it('port_conflict 且没有 detail：回退到「端口冲突，请关闭占用进程」', async () => {
    mocks.post.mockResolvedValueOnce({
      data: chromeStatus({ state: 'port_conflict', detail: '', pid: null }),
    })
    const manager = useChromeManager()

    await manager.startChrome()

    expect(Message.error).toHaveBeenCalledWith('端口冲突，请关闭占用进程')
  })

  it('starting 等非终态：warning 提示，detail 为空时回退「启动失败」', async () => {
    mocks.post.mockResolvedValueOnce({
      data: chromeStatus({ state: 'starting', detail: '等待端口就绪', pid: null }),
    })
    const manager = useChromeManager()
    await manager.startChrome()
    expect(Message.warning).toHaveBeenCalledWith('等待端口就绪')

    mocks.post.mockResolvedValueOnce({
      data: chromeStatus({ state: 'starting', detail: '', pid: null }),
    })
    await manager.startChrome()
    expect(Message.warning).toHaveBeenLastCalledWith('启动失败')
  })

  it('请求失败：提示拼接后端 detail，busy 复位', async () => {
    mocks.post.mockRejectedValueOnce({
      response: { data: { detail: '启动脚本超时' } },
    })
    const manager = useChromeManager()

    await manager.startChrome()

    expect(Message.error).toHaveBeenCalledWith('启动 Chrome 失败: 启动脚本超时')
    expect(manager.chromeBusy.value).toBe(false)
  })

  it('请求进行中 chromeBusy=true，完成后复位（按钮 loading 语义）', async () => {
    let resolveRequest!: (value: unknown) => void
    mocks.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve
        }),
    )
    const manager = useChromeManager()

    const pending = manager.startChrome()
    expect(manager.chromeBusy.value).toBe(true)

    resolveRequest({ data: chromeStatus() })
    await pending

    expect(manager.chromeBusy.value).toBe(false)
  })
})

describe('stopChrome', () => {
  it('成功：写入状态并提示「Chrome 已停止」', async () => {
    const status = chromeStatus({ state: 'not_started', pid: null })
    mocks.post.mockResolvedValueOnce({ data: status })
    const manager = useChromeManager()

    await manager.stopChrome()

    expect(mocks.post).toHaveBeenCalledWith('/scraper/chrome/stop')
    expect(manager.chromeStatus.value).toEqual(status)
    expect(Message.success).toHaveBeenCalledWith('Chrome 已停止')
    expect(manager.chromeBusy.value).toBe(false)
  })

  it('失败：提示拼接后端 detail，busy 复位', async () => {
    mocks.post.mockRejectedValueOnce({
      response: { data: { detail: '进程不存在' } },
    })
    const manager = useChromeManager()

    await manager.stopChrome()

    expect(Message.error).toHaveBeenCalledWith('停止 Chrome 失败: 进程不存在')
    expect(manager.chromeBusy.value).toBe(false)
  })
})
