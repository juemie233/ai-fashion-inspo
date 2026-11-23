/**
 * 剪贴板工具：异步 Clipboard API 优先，window.execCommand 回退，返回是否成功。
 *
 * 这个工具被多个页面用来做「复制链接 / 复制提示词」，失败会弹「复制失败」：
 * - Clipboard API 可用（HTTPS/localhost）时必须走它，不能无故走已被废弃的 execCommand；
 * - Clipboard API 被拒绝（权限/非安全上下文）时要回退，且回退用的 textarea
 *   必须立刻从 DOM 移除（否则页面会多出一个隐藏输入框、还可能抢焦点）；
 * - 两条路都失败时返回 false，调用方据此提示，不能抛异常。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { copyToClipboard } from '../clipboard'

const writeText = vi.fn()

/** 注入 navigator.clipboard（happy-dom 原型上只有 getter，defineProperty 到实例可用） */
function setClipboard(value: unknown) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}

/** 注入 document.execCommand（happy-dom 默认没有实现） */
function setExecCommand(impl: () => boolean) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value: impl })
}

beforeEach(() => {
  vi.clearAllMocks()
  writeText.mockResolvedValue(undefined)
  setClipboard({ writeText })
})

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard')
  Reflect.deleteProperty(document, 'execCommand')
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('copyToClipboard', () => {
  it('Clipboard API 可用时直接写入并返回 true，不走 execCommand', async () => {
    const execCommand = vi.fn(() => true)
    setExecCommand(execCommand)

    await expect(copyToClipboard('https://example.com/a?b=1')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith('https://example.com/a?b=1')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('Clipboard API 拒绝时回退 execCommand，textarea 带正确内容且用后移除', async () => {
    writeText.mockRejectedValue(new Error('NotAllowedError'))
    const seenValues: string[] = []
    const execCommand = vi.fn(() => {
      // execCommand 执行时 textarea 应已挂到 body 且选中
      const textarea = document.querySelector('textarea')
      seenValues.push(textarea?.value ?? '<missing>')
      return true
    })
    setExecCommand(execCommand)

    await expect(copyToClipboard('回退文本')).resolves.toBe(true)

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(seenValues).toEqual(['回退文本'])
    // 成功路径必须清理临时 textarea
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('navigator.clipboard 不存在时回退 execCommand', async () => {
    setClipboard(undefined)
    const execCommand = vi.fn(() => true)
    setExecCommand(execCommand)

    await expect(copyToClipboard('fallback')).resolves.toBe(true)

    expect(writeText).not.toHaveBeenCalled()
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('回退 execCommand 返回 false 时整体返回 false', async () => {
    writeText.mockRejectedValue(new Error('denied'))
    setExecCommand(() => false)

    await expect(copyToClipboard('x')).resolves.toBe(false)
  })

  it('两条路都抛异常时返回 false，不向调用方抛错', async () => {
    writeText.mockRejectedValue(new Error('denied'))
    setExecCommand(() => {
      throw new DOMException('copy failed')
    })

    await expect(copyToClipboard('x')).resolves.toBe(false)
  })
})
