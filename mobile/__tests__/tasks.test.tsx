/** 任务中心：任务行渲染 + 任务页加载/取消/空态。 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'
import TaskRow from '../components/TaskRow'
import TasksScreen from '../app/tasks'
import { findBareText } from '../test-utils/noBareText'
import type { TaskItem } from '../services/api'

jest.mock('../services/api', () => ({
  fetchTasks: jest.fn(),
  cancelTask: jest.fn(),
}))

// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchTasks: jest.Mock
  cancelTask: jest.Mock
}

function makeTask(over: Partial<TaskItem> = {}): TaskItem {
  return {
    id: 1,
    type: 'batch_analyze',
    status: 'running',
    progress: 40,
    total: 10,
    done: 4,
    error: null,
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    ...over,
  } as TaskItem
}

describe('TaskRow', () => {
  it('把任务类型与状态翻译成中文，并显示进度', () => {
    const utils = render(<TaskRow task={makeTask()} onCancel={jest.fn()} />)

    expect(screen.getByText('批量 AI 分析')).toBeTruthy()
    expect(screen.getByText('进行中')).toBeTruthy()
    expect(screen.getByText('4/10 · 40%')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('进行中的任务可取消，回调带任务 id', () => {
    const onCancel = jest.fn()
    render(<TaskRow task={makeTask({ id: 9 })} onCancel={onCancel} />)

    fireEvent.press(screen.getByText('取消'))
    expect(onCancel).toHaveBeenCalledWith(9)
  })

  it('已完成的任务不显示取消按钮', () => {
    render(<TaskRow task={makeTask({ status: 'success', progress: 100 })} onCancel={jest.fn()} />)

    expect(screen.getByText('已完成')).toBeTruthy()
    expect(screen.queryByText('取消')).toBeNull()
  })

  it('失败任务显示错误原因', () => {
    render(
      <TaskRow task={makeTask({ status: 'failed', error: '模型未启动' })} onCancel={jest.fn()} />,
    )

    expect(screen.getByText('失败')).toBeTruthy()
    expect(screen.getByText('模型未启动')).toBeTruthy()
  })

  it('后端队列任务类型都能翻成中文（抽查几个真实 task_queue.type）', () => {
    // 只列后端 TASK_HANDLERS 里真实存在的类型：scraper_tasks 是**表名**
    // （采集任务在 scraper_tasks 表，不在 task_queue），不该出现在这里
    const cases: [string, string][] = [
      ['batch_analyze', '批量 AI 分析'],
      ['image_gen', '本地出图'],
      ['vector_backfill', '向量回填'],
      ['quality_check', '质量审核'],
      ['f2_import', '抖音素材获取'],
      ['blogger_avatar_fill', '人物头像补全'],
    ]
    for (const [type, label] of cases) {
      const { unmount } = render(
        <TaskRow task={makeTask({ type, status: 'running' })} onCancel={jest.fn()} />,
      )
      expect(screen.getByText(label)).toBeTruthy()
      unmount()
    }
  })

  it('已暂停的任务也能取消（后端对 paused 直接置 cancelled）', () => {
    render(<TaskRow task={makeTask({ type: 'face_scan', status: 'paused' })} onCancel={jest.fn()} />)

    expect(screen.getByText('已暂停')).toBeTruthy()
    expect(screen.getByText('取消')).toBeTruthy()
  })

  it('未知类型/状态回退原值，不显示空白', () => {
    render(<TaskRow task={makeTask({ type: 'brand_new', status: 'weird' })} onCancel={jest.fn()} />)

    expect(screen.getByText('brand_new')).toBeTruthy()
    expect(screen.getByText('weird')).toBeTruthy()
  })

  it('暂停中的任务显示「已暂停」', () => {
    render(<TaskRow task={makeTask({ status: 'paused' })} onCancel={jest.fn()} />)
    expect(screen.getByText('已暂停')).toBeTruthy()
  })

  it('progress 缺失时用 done/total 反算比例', () => {
    render(
      <TaskRow
        task={makeTask({ progress: 0, total: 4, done: 1 })}
        onCancel={jest.fn()}
      />,
    )
    expect(screen.getByText('1/4 · 25%')).toBeTruthy()
  })
})

describe('TasksScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('加载成功时渲染任务列表', async () => {
    api.fetchTasks.mockResolvedValue({
      items: [makeTask({ id: 1 }), makeTask({ id: 2, status: 'success', progress: 100 })],
      total: 2,
      page: 1,
      size: 30,
    })

    const utils = render(<TasksScreen />)
    // 两条任务都是 AI 分析类型，这里断言两行都渲染出来
    await waitFor(() => expect(screen.getAllByText('批量 AI 分析')).toHaveLength(2))

    expect(api.fetchTasks).toHaveBeenCalledWith(1, 30)
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('加载失败显示错误提示', async () => {
    api.fetchTasks.mockRejectedValue(new Error('boom'))

    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByText('加载失败，请检查网络或后端服务')).toBeTruthy())
  })

  it('空列表显示暂无任务', async () => {
    api.fetchTasks.mockResolvedValue({ items: [], total: 0, page: 1, size: 30 })

    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByText('暂无后台任务')).toBeTruthy())
  })

  it('取消任务需二次确认，确认后调接口并刷新', async () => {
    api.fetchTasks.mockResolvedValue({ items: [makeTask({ id: 5 })], total: 1, page: 1, size: 30 })
    api.cancelTask.mockResolvedValue(undefined)
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => {})

    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByText('取消')).toBeTruthy())

    fireEvent.press(screen.getByText('取消'))
    expect(alertSpy).toHaveBeenCalled()

    // 取确认弹窗里的「确定取消」按钮并执行
    const buttons = alertSpy.mock.calls[0][2] as { text: string; onPress?: () => void }[]
    const confirm = buttons.find((b) => b.text === '确定取消')
    expect(confirm).toBeTruthy()

    await act(async () => {
      confirm?.onPress?.()
    })

    await waitFor(() => expect(api.cancelTask).toHaveBeenCalledWith(5), { timeout: 5000 })
    // 取消后重新拉列表（首次 + 刷新）
    await waitFor(() => expect(api.fetchTasks).toHaveBeenCalledTimes(2), { timeout: 5000 })

    alertSpy.mockRestore()
  })
})
