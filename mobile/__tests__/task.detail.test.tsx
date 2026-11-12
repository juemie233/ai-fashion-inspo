/** 任务详情页（P0-2）：概要 + 阶段/摘要 + 逐次运行台账。 */

import { render, screen, waitFor } from '@testing-library/react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '42' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => ({
  fetchTask: jest.fn(),
  fetchTaskRuns: jest.fn(),
}))

import TaskDetailScreen from '../app/task/[id]'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as { fetchTask: jest.Mock; fetchTaskRuns: jest.Mock }

function makeTask(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    type: 'f2_import',
    status: 'running',
    progress: 20,
    total: 21,
    done: 4,
    error: null,
    result: { stage: 'download' },
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    ...over,
  }
}

function makeRun(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    task_id: 42,
    task_type: 'f2_import',
    attempt: 0,
    started_at: '2026-10-24T00:00:00Z',
    finished_at: '2026-10-24T00:03:00Z',
    item_count: 4,
    total_count: 21,
    status: 'success',
    gpu_model: null,
    error: null,
    ...over,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe('TaskDetailScreen', () => {
  it('渲染类型、状态、进度、阶段与运行台账', async () => {
    api.fetchTask.mockResolvedValue(makeTask())
    api.fetchTaskRuns.mockResolvedValue([makeRun()])

    const utils = render(<TaskDetailScreen />)

    await waitFor(() => expect(screen.getByText('抖音素材获取')).toBeTruthy())
    expect(screen.getByText('进行中')).toBeTruthy()
    expect(screen.getByText('进度 4/21 · 20%')).toBeTruthy()
    expect(screen.getByText('下载中：第 4/21')).toBeTruthy()
    // 台账：第 1 次 · 成功 · 4/21 项 · 3 分
    expect(screen.getByText('第 1 次 · 成功')).toBeTruthy()
    expect(screen.getByText('4/21 项')).toBeTruthy()
    expect(screen.getByTestId('run-1')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('运行中展示「第 N 次」并带 GPU 与失败原因', async () => {
    api.fetchTask.mockResolvedValue(makeTask({ status: 'success', progress: 100, result: null }))
    api.fetchTaskRuns.mockResolvedValue([
      makeRun({ id: 2, attempt: 1, status: 'failed', gpu_model: 'RTX 5060 Ti', error: 'Ollama 未启动' }),
    ])

    render(<TaskDetailScreen />)

    await waitFor(() => expect(screen.getByText('第 2 次 · 失败')).toBeTruthy())
    expect(screen.getByText('RTX 5060 Ti')).toBeTruthy()
    expect(screen.getByText('Ollama 未启动')).toBeTruthy()
  })

  it('没有台账时给出空态说明', async () => {
    api.fetchTask.mockResolvedValue(makeTask({ status: 'pending' }))
    api.fetchTaskRuns.mockResolvedValue([])

    render(<TaskDetailScreen />)

    await waitFor(() => expect(screen.getByText('还没有运行记录')).toBeTruthy())
  })

  it('加载失败给出错误态与重试', async () => {
    api.fetchTask.mockRejectedValue(new Error('boom'))
    api.fetchTaskRuns.mockResolvedValue([])

    render(<TaskDetailScreen />)

    await waitFor(() => expect(screen.getByText('加载失败')).toBeTruthy())
    expect(screen.getByTestId('retry-button')).toBeTruthy()
  })

  it('运行台账接口失败不影响主内容（降级为空列表）', async () => {
    api.fetchTask.mockResolvedValue(makeTask())
    api.fetchTaskRuns.mockRejectedValue(new Error('runs down'))

    render(<TaskDetailScreen />)

    await waitFor(() => expect(screen.getByText('抖音素材获取')).toBeTruthy())
    expect(screen.getByText('还没有运行记录')).toBeTruthy()
  })
})
