/**
 * 定时采集计划域（`useScraperSchedules`）的测试。
 *
 * 它是「定时采集」页签的全部交互逻辑，原覆盖率 2.7%。三个容易写错又不会报错的点：
 *
 * 1. **轮换关键词要拆分**：表单里用户可能输入「法式,通勤」或「复古、运动」，
 *    每条都要按半角/全角逗号、顿号拆开去空白，否则会变成一个整词，后端按词轮换失效；
 * 2. **排序方式只对小红书生效**：「综合」不传（后端归一化为 NULL），抖音永远不带
 *    sort_mode——传了后端不认、列表筛选也会对不上；
 * 3. **启停文案按「操作前状态」取反**：`已停用` / `已启用` 说反了用户会以为没生效。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  extractHistoryKeywords: vi.fn(() => ['法式', '通勤']),
}))

vi.mock('@/api/client', () => ({
  default: { get: mocks.get, post: mocks.post, patch: mocks.patch, delete: mocks.del },
}))

vi.mock('@/utils/scraperKeywords', () => ({
  extractHistoryKeywords: mocks.extractHistoryKeywords,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { INTERVAL_OPTIONS, intervalLabel, useScraperSchedules } from '../useScraperSchedules'

type Schedule = Parameters<ReturnType<typeof useScraperSchedules>['toggleSchedule']>[0]

function schedule(overrides: Record<string, unknown> = {}): Schedule {
  return {
    id: 1,
    platform: 'xiaohongshu',
    keywords: '["法式"]',
    max_count: 20,
    sort_mode: null,
    interval_minutes: 1440,
    enabled: true,
    next_run_at: null,
    last_run_at: null,
    run_count: 0,
    ...overrides,
  } as unknown as Schedule
}

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
}

/** 该 composable 在 setup 外调用会触发 onMounted 警告，测试里静默掉 */
function setup() {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const api = useScraperSchedules()
  warn.mockRestore()
  return api
}

describe('useScraperSchedules', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: [] })
    mocks.post.mockResolvedValue({ data: { task_id: 42 } })
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.del.mockResolvedValue({ data: {} })
  })

  it('intervalLabel：已知间隔给中文，未知回退「N 分钟」', () => {
    expect(intervalLabel(60)).toBe('每 1 小时')
    expect(intervalLabel(1440)).toBe('每天')
    expect(intervalLabel(999)).toBe('999 分钟')
    expect(INTERVAL_OPTIONS.some((o) => o.value === 1440)).toBe(true)
  })

  it('表单默认值：小红书 / 每天 / 20 条 / 综合 / 启用', () => {
    const s = setup()
    expect(s.formPlatform.value).toBe('xiaohongshu')
    expect(s.formKeywords.value).toEqual([])
    expect(s.formMaxCount.value).toBe(20)
    expect(s.formSortMode.value).toBe('general')
    expect(s.formInterval.value).toBe(1440)
    expect(s.formEnabled.value).toBe(true)
  })

  describe('加载', () => {
    it('loadSchedules 成功写入列表，loading 过程态正确', async () => {
      const rows = [schedule(), schedule({ id: 2 })]
      mocks.get.mockResolvedValue({ data: rows })
      const s = setup()

      const promise = s.loadSchedules()
      expect(s.loading.value).toBe(true)
      await promise

      expect(s.schedules.value).toHaveLength(2)
      expect(s.loading.value).toBe(false)
    })

    it('loadSchedules 失败提示并复位 loading', async () => {
      mocks.get.mockRejectedValue(null)
      const s = setup()

      await s.loadSchedules()

      expect(mocks.message.error).toHaveBeenCalledWith('加载定时计划失败')
      expect(s.loading.value).toBe(false)
    })

    it('挂载时自动加载历史关键词；失败静默降级', async () => {
      // loadHistoryKeywords 未导出，由 onMounted 触发：用真实组件挂载验证接线
      mocks.get.mockResolvedValue({ data: { items: [{ id: 1 }] } })
      let api: ReturnType<typeof useScraperSchedules> | null = null
      const App = defineComponent({
        setup() {
          api = useScraperSchedules()
          return () => null
        },
      })
      const wrapper = mount(App)
      await flush()

      expect(mocks.get).toHaveBeenCalledWith('/scraper/tasks', {
        params: { sort: 'newest', size: 200 },
      })
      expect(mocks.extractHistoryKeywords).toHaveBeenCalledWith([{ id: 1 }])
      expect(api!.historyKeywords.value).toEqual(['法式', '通勤'])
      expect(api!.keywordOptions.value).toEqual([
        { label: '法式', value: '法式' },
        { label: '通勤', value: '通勤' },
      ])
      wrapper.unmount()

      // 失败时静默降级，不影响手动输入
      mocks.get.mockRejectedValue(new Error('500'))
      const failing = mount(App)
      await flush()
      expect(mocks.message.error).not.toHaveBeenCalled()
      failing.unmount()
    })
  })

  describe('创建计划', () => {
    it('关键词按逗号/顿号拆分去空白，小红书带 sort_mode', async () => {
      const s = setup()
      s.formKeywords.value = ['法式, 通勤', '复古、运动']
      s.formSortMode.value = 'popular'

      await s.createSchedule()

      expect(mocks.post).toHaveBeenCalledWith('/scraper/schedules', {
        platform: 'xiaohongshu',
        keywords: ['法式', '通勤', '复古', '运动'],
        max_count: 20,
        interval_minutes: 1440,
        enabled: true,
        sort_mode: 'popular',
      })
      expect(mocks.message.success).toHaveBeenCalledWith('定时计划已创建')
      expect(s.formKeywords.value).toEqual([]) // 成功后清空关键词输入
      expect(s.creating.value).toBe(false)
    })

    it('「综合」不传 sort_mode；抖音永不传', async () => {
      const s = setup()
      s.formKeywords.value = ['a']
      s.formSortMode.value = 'general'
      await s.createSchedule()
      expect(mocks.post.mock.calls[0][1]).not.toHaveProperty('sort_mode')

      s.formPlatform.value = 'douyin'
      s.formSortMode.value = 'popular'
      await s.createSchedule()
      expect(mocks.post.mock.calls[1][1]).not.toHaveProperty('sort_mode')
    })

    it('创建失败提示且复位 creating', async () => {
      mocks.post.mockRejectedValue(null)
      const s = setup()
      s.formKeywords.value = ['a']

      await s.createSchedule()

      expect(mocks.message.error).toHaveBeenCalledWith('创建失败')
      expect(s.creating.value).toBe(false)
    })
  })

  describe('更新计划', () => {
    it('拆分关键词并返回 true，触发列表刷新', async () => {
      const s = setup()

      const ok = await s.updateSchedule(3, {
        keywords: ['法式,通勤'],
        max_count: 50,
        sort_mode: 'latest',
        interval_minutes: 360,
      })

      expect(ok).toBe(true)
      expect(mocks.patch).toHaveBeenCalledWith('/scraper/schedules/3', {
        keywords: ['法式', '通勤'],
        max_count: 50,
        interval_minutes: 360,
        sort_mode: 'latest',
      })
      expect(mocks.message.success).toHaveBeenCalledWith('定时计划已更新')
      expect(s.updatingId.value).toBeNull()
    })

    it('更新失败返回 false 并提示', async () => {
      mocks.patch.mockRejectedValue(null)
      const s = setup()

      const ok = await s.updateSchedule(3, {
        keywords: ['a'],
        max_count: 20,
        sort_mode: null,
        interval_minutes: 1440,
      })

      expect(ok).toBe(false)
      expect(mocks.message.error).toHaveBeenCalledWith('更新失败')
      expect(s.updatingId.value).toBeNull()
    })
  })

  describe('启停 / 立即执行 / 删除', () => {
    it('启用中的计划点一下 → 停用（文案说清操作结果）', async () => {
      const s = setup()
      await s.toggleSchedule(schedule({ enabled: true }))

      expect(mocks.patch).toHaveBeenCalledWith('/scraper/schedules/1', { enabled: false })
      expect(mocks.message.success).toHaveBeenCalledWith('已停用')
      expect(s.togglingId.value).toBeNull()
    })

    it('已停用的计划点一下 → 启用', async () => {
      const s = setup()
      await s.toggleSchedule(schedule({ enabled: false }))

      expect(mocks.patch).toHaveBeenCalledWith('/scraper/schedules/1', { enabled: true })
      expect(mocks.message.success).toHaveBeenCalledWith('已启用')
    })

    it('启停失败提示「操作失败」', async () => {
      mocks.patch.mockRejectedValue(null)
      const s = setup()

      await s.toggleSchedule(schedule())

      expect(mocks.message.error).toHaveBeenCalledWith('操作失败')
      expect(s.togglingId.value).toBeNull()
    })

    it('立即执行：提示任务号并刷新；失败提示触发失败', async () => {
      const s = setup()
      await s.runNow(schedule({ id: 7 }))
      expect(mocks.post).toHaveBeenCalledWith('/scraper/schedules/7/run')
      expect(mocks.message.success).toHaveBeenCalledWith('已触发采集任务 #42')
      expect(s.runningId.value).toBeNull()

      mocks.post.mockRejectedValue(null)
      await s.runNow(schedule({ id: 8 }))
      expect(mocks.message.error).toHaveBeenCalledWith('触发失败')
      expect(s.runningId.value).toBeNull()
    })

    it('删除：成功提示并刷新；失败复位 deletingId', async () => {
      const s = setup()
      await s.deleteSchedule(schedule({ id: 5 }))
      expect(mocks.del).toHaveBeenCalledWith('/scraper/schedules/5')
      expect(mocks.message.success).toHaveBeenCalledWith('已删除')
      expect(s.deletingId.value).toBeNull()

      mocks.del.mockRejectedValue(null)
      await s.deleteSchedule(schedule({ id: 6 }))
      expect(mocks.message.error).toHaveBeenCalledWith('删除失败')
      expect(s.deletingId.value).toBeNull()
    })
  })
})
