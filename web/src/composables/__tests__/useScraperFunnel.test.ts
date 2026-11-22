/**
 * 采集漏斗弹窗（`useScraperFunnel`）的测试。
 *
 * 漏斗数据是后端写进 `task.diagnostics` 的 JSON 字符串（旧版本任务没有），
 * 这里的三条分支决定弹窗表现：正常解析打开、无数据只警告、坏 JSON 报错且不打开。
 * 另外 `funnelOpen` 是 computed setter：关闭时必须同时清空 id 与数据（下次打开不能闪旧图）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

import { useScraperFunnel } from '../useScraperFunnel'
import type { ScraperTask } from '@/types/scraper'

function task(overrides: Partial<ScraperTask> = {}): ScraperTask {
  return {
    id: 1,
    platform: 'xiaohongshu',
    status: 'success',
    config: null,
    items_found: 10,
    items_added: 3,
    created_at: '2026-11-20T10:00:00Z',
    ...overrides,
  }
}

const DIAGNOSTICS = JSON.stringify({
  per_search: [{ keyword: '法式', sort_type: 'general' }],
  summary: {
    total_found: 10,
    skipped_url_seen: 2,
    skipped_content_dup: 1,
    skipped_http_error: 0,
    skipped_network_error: 0,
    total_added: 3,
  },
})

describe('useScraperFunnel', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('初始关闭：无任务 id、无数据', () => {
    const funnel = useScraperFunnel()

    expect(funnel.funnelTaskId.value).toBeNull()
    expect(funnel.funnelData.value).toBeNull()
    expect(funnel.funnelOpen.value).toBe(false)
  })

  it('打开任务：解析 diagnostics JSON 并回填 id / 数据', () => {
    const funnel = useScraperFunnel()

    funnel.viewFunnel(task({ id: 7, diagnostics: DIAGNOSTICS }))

    expect(funnel.funnelTaskId.value).toBe(7)
    expect(funnel.funnelData.value?.summary.total_added).toBe(3)
    expect(funnel.funnelData.value?.per_search[0].keyword).toBe('法式')
    expect(funnel.funnelOpen.value).toBe(true)
  })

  it('再次点击同一任务 = 收起并清空数据', () => {
    const funnel = useScraperFunnel()
    const same = task({ id: 7, diagnostics: DIAGNOSTICS })
    funnel.viewFunnel(same)

    funnel.viewFunnel(same)

    expect(funnel.funnelTaskId.value).toBeNull()
    expect(funnel.funnelData.value).toBeNull()
    expect(funnel.funnelOpen.value).toBe(false)
  })

  it('切换到另一个任务：数据被替换', () => {
    const funnel = useScraperFunnel()
    funnel.viewFunnel(task({ id: 1, diagnostics: DIAGNOSTICS }))

    const other = JSON.stringify({
      per_search: [],
      summary: {
        total_found: 99,
        skipped_url_seen: 0,
        skipped_content_dup: 0,
        skipped_http_error: 0,
        skipped_network_error: 0,
        total_added: 9,
      },
    })
    funnel.viewFunnel(task({ id: 2, diagnostics: other }))

    expect(funnel.funnelTaskId.value).toBe(2)
    expect(funnel.funnelData.value?.summary.total_found).toBe(99)
  })

  it('旧任务没有 diagnostics：只警告，不开弹窗', () => {
    const warning = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const funnel = useScraperFunnel()

    funnel.viewFunnel(task({ id: 3, diagnostics: null }))

    expect(warning).toHaveBeenCalledWith('该任务无漏斗数据（旧版本采集的任务）')
    expect(funnel.funnelTaskId.value).toBeNull()
    expect(funnel.funnelData.value).toBeNull()
  })

  it('diagnostics 是坏 JSON：报错且不打开、不残留旧数据', () => {
    const error = vi.spyOn(Message, 'error').mockImplementation((() => {}) as never)
    const funnel = useScraperFunnel()
    // 先打开一个合法任务，确认坏数据不会改成它
    funnel.viewFunnel(task({ id: 1, diagnostics: DIAGNOSTICS }))

    funnel.viewFunnel(task({ id: 2, diagnostics: '{broken' }))

    expect(error).toHaveBeenCalledWith('漏斗数据解析失败')
    expect(funnel.funnelTaskId.value).toBe(1)
    expect(funnel.funnelData.value?.summary.total_added).toBe(3)
  })

  it('funnelOpen 置 false 清空状态；无 id 时置 true 不会打开', () => {
    const funnel = useScraperFunnel()
    funnel.viewFunnel(task({ id: 5, diagnostics: DIAGNOSTICS }))

    funnel.funnelOpen.value = false
    expect(funnel.funnelTaskId.value).toBeNull()
    expect(funnel.funnelData.value).toBeNull()

    funnel.funnelOpen.value = true
    expect(funnel.funnelTaskId.value).toBeNull()
    expect(funnel.funnelOpen.value).toBe(false)
  })
})
