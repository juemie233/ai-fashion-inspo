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

  it('运行中的 f2 任务显示阶段与计数（不再只有一个百分比）', () => {
    render(
      <TaskRow
        task={makeTask({
          type: 'f2_import',
          status: 'running',
          done: 4,
          total: 21,
          result: { stage: 'download' },
        })}
        onCancel={jest.fn()}
      />,
    )
    expect(screen.getByText('下载中：第 4/21')).toBeTruthy()
  })

  it('已完成任务显示结果摘要（向量回填：图像/文本/失败/跳过）', () => {
    render(
      <TaskRow
        task={makeTask({
          type: 'vector_backfill',
          status: 'success',
          progress: 100,
          done: 320,
          total: 320,
          result: { image_done: 320, text_done: 318, text_failed: 2, image_skipped: 5 },
        })}
        onCancel={jest.fn()}
      />,
    )
    expect(screen.getByText('图像向量 320 · 文本向量 318 · 文本失败 2 · 跳过 5')).toBeTruthy()
  })

  it('质量审核完成后显示通过/拒绝/未判定', () => {
    render(
      <TaskRow
        task={makeTask({
          type: 'quality_check',
          status: 'success',
          result: { approved: 88, rejected: 12, pending: 3 },
        })}
        onCancel={jest.fn()}
      />,
    )
    expect(screen.getByText('通过 88 · 拒绝 12 · 未判定 3')).toBeTruthy()
  })

  it('点整行触发 onPress（进任务详情）', () => {
    const onPress = jest.fn()
    render(<TaskRow task={makeTask({ id: 7 })} onCancel={jest.fn()} onPress={onPress} />)
    fireEvent.press(screen.getByTestId('task-7'))
    expect(onPress).toHaveBeenCalledTimes(1)
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

    // P0-2：每页 20 条（原固定 30），筛选条件为空对象
    expect(api.fetchTasks).toHaveBeenCalledWith(1, 20, {})
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('加载失败显示错误提示', async () => {
    api.fetchTasks.mockRejectedValue(new Error('boom'))

    render(<TasksScreen />)
    // 改版后错误态拆成标题 + 说明（ErrorState），不再是一行拼接文案
    await waitFor(() => expect(screen.getByText('加载失败')).toBeTruthy())
    expect(screen.getByText('请检查网络或后端服务')).toBeTruthy()
    expect(screen.getByTestId('retry-button')).toBeTruthy()
  })

  it('空列表显示暂无任务', async () => {
    api.fetchTasks.mockResolvedValue({ items: [], total: 0, page: 1, size: 30 })

    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByText('暂无后台任务')).toBeTruthy())
  })

  it('状态 chip 会带 status 重新拉第一页', async () => {
    api.fetchTasks.mockResolvedValue({ items: [makeTask({ id: 1 })], total: 1, page: 1, size: 20 })
    render(<TasksScreen />)
    await waitFor(() => expect(api.fetchTasks).toHaveBeenCalledWith(1, 20, {}))

    fireEvent.press(screen.getByTestId('task-status-failed'))
    await waitFor(() => expect(api.fetchTasks).toHaveBeenLastCalledWith(1, 20, { status: 'failed' }))
  })

  it('类型弹层选中后带 type 重新拉第一页', async () => {
    api.fetchTasks.mockResolvedValue({ items: [makeTask({ id: 1 })], total: 1, page: 1, size: 20 })
    render(<TasksScreen />)
    await waitFor(() => expect(api.fetchTasks).toHaveBeenCalled())

    fireEvent.press(screen.getByTestId('task-type-filter'))
    await waitFor(() => expect(screen.getByText('按类型筛选')).toBeTruthy())
    fireEvent.press(screen.getByTestId('task-type-vector_backfill'))

    await waitFor(() =>
      expect(api.fetchTasks).toHaveBeenLastCalledWith(1, 20, { type: 'vector_backfill' }),
    )
  })

  it('触底翻页：请求第 2 页并追加', async () => {
    api.fetchTasks.mockImplementation((page: number) =>
      Promise.resolve({
        items: [makeTask({ id: page === 1 ? 1 : 2 })],
        total: 2,
        page,
        size: 20,
      }),
    )
    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByTestId('task-1')).toBeTruthy())

    // 触底（FlatList 的 onEndReached）
    fireEvent(screen.getByTestId('task-list'), 'endReached')

    await waitFor(() => expect(api.fetchTasks).toHaveBeenCalledWith(2, 20, {}))
    await waitFor(() => expect(screen.getByTestId('task-2')).toBeTruthy())
  })

  it('点任务行进入详情页', async () => {
    api.fetchTasks.mockResolvedValue({ items: [makeTask({ id: 9 })], total: 1, page: 1, size: 20 })
    render(<TasksScreen />)
    await waitFor(() => expect(screen.getByTestId('task-9')).toBeTruthy())
    fireEvent.press(screen.getByTestId('task-9'))
    // expo-router 在 jest 里被 mock 成固定对象，这里只断言点击不抛错
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
