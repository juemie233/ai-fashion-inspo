/**
 * 任务列表（TaskList）的按钮矩阵测试。
 *
 * 数据来自真实 `UnifiedTask` 形状，断言「某个 status/source 的行会显示哪些按钮」。
 * 这类测试锁的是**用户能看到什么**：按钮该有的没有 → 任务没法停；不该有的有 →
 * 点了拿到后端 400。
 *
 * 与 `taskRowActions` 用例的分工：那边测纯函数（含与后端白名单的逐条对齐），
 * 这边测组件内联的渲染条件（终态/paused 的「删除任务」按钮是组件单独判的，
 * 不走 taskRowActions.canDelete）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

vi.mock('@/api/aiModelStats', () => ({
  fetchModelStats: vi.fn().mockResolvedValue([]),
  overallAvgMs: () => null,
}))

import TaskList from '../TaskList.vue'
import type { UnifiedTask } from '@/types/task'

function queueTask(over: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    key: 'q1',
    source: 'queue',
    id: 1,
    type: 'batch_analyze',
    status: 'running',
    progress: 10,
    total: 100,
    done: 10,
    error: null,
    result: null,
    created_at: '2026-10-21T10:00:00',
    updated_at: '2026-10-21T10:01:00',
    ...over,
  } as UnifiedTask
}

function scraperTask(over: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    ...queueTask(),
    key: 's1',
    source: 'scraper',
    id: 2,
    type: 'scraper',
    status: 'running',
    ...over,
  } as UnifiedTask
}

function mountList(tasks: UnifiedTask[]) {
  return mount(TaskList, {
    props: { tasks, loading: false },
    global: { plugins: [ArcoVue] },
  })
}

/** 表格里所有按钮的文案（Arco 表格渲染后按 DOM 取）。 */
function buttonTexts(wrapper: ReturnType<typeof mountList>): string[] {
  return wrapper
    .findAll('button')
    .map((b) => b.text().trim())
    .filter(Boolean)
}

describe('TaskList 按钮矩阵', () => {
  beforeEach(() => vi.clearAllMocks())

  it('运行中 + 可取消类型：暂停/取消/删除任务三个按钮都在', async () => {
    // 「删除任务」在运行中也显示是有意的：僵尸任务（worker 崩溃/停电）只能靠心跳
    // 超时后删除，正常执行中的会被后端 400 拒绝（popconfirm 文案已说明）。
    const wrapper = mountList([queueTask({ status: 'running', type: 'batch_analyze' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).toEqual(expect.arrayContaining(['暂停', '取消', '删除任务']))
  })

  it('运行中 + 不可取消类型：没有「取消」，但有「删除任务」（僵尸任务由后端心跳裁决）', async () => {
    const wrapper = mountList([queueTask({ status: 'running', type: 'batch_delete' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).not.toContain('取消')
    expect(texts).toContain('删除任务')
  })

  it('已暂停：既有「恢复」也有「删除任务」', async () => {
    const wrapper = mountList([queueTask({ status: 'paused' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).toContain('恢复')
    expect(texts).toContain('删除任务')
  })

  it('终态（success/failed/cancelled）：只有「删除任务」，没有取消/恢复', async () => {
    for (const status of ['success', 'failed', 'cancelled']) {
      const wrapper = mountList([queueTask({ status })])
      await flushPromises()

      const texts = buttonTexts(wrapper)
      expect(texts, status).toContain('删除任务')
      expect(texts, status).not.toContain('取消')
      expect(texts, status).not.toContain('恢复')
    }
  })

  it('pending：出现「删除」入口（对 pending 任务，取消即物理删除）', async () => {
    const wrapper = mountList([queueTask({ status: 'pending' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).toContain('删除')
    expect(texts).not.toContain('取消')
    expect(texts).not.toContain('删除任务')
  })

  it('出图任务运行中：可取消但不可暂停（image_gen 只在取消白名单）', async () => {
    const wrapper = mountList([queueTask({ status: 'running', type: 'image_gen' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).toContain('取消')
    expect(texts).not.toContain('暂停')
  })

  it('采集任务：显示「删除」（采集专用接口），不显示队列的「删除任务」', async () => {
    const wrapper = mountList([scraperTask({ status: 'running' })])
    await flushPromises()

    const texts = buttonTexts(wrapper)
    expect(texts).toContain('删除')
    expect(texts).not.toContain('删除任务')
  })

  it('空列表不报错', async () => {
    const wrapper = mountList([])
    await flushPromises()

    expect(wrapper.text()).toContain('暂无数据')
  })
})
