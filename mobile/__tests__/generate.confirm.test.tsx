/** 出图候选收尾（P1）：勾选入库、跳过原因、丢弃整批。 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

const mockRouter = { push: jest.fn(), back: jest.fn(), replace: jest.fn() }

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '7' }),
  useRouter: () => mockRouter,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    fetchGenerateRun: jest.fn(),
    fetchGenerateModelLabels: jest.fn(async () => ({})),
    confirmGenerateCandidates: jest.fn(),
    discardGenerateRun: jest.fn(),
  }
})

import GenerateRunScreen from '../app/generate/[id]'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchGenerateRun: jest.Mock
  confirmGenerateCandidates: jest.Mock
  discardGenerateRun: jest.Mock
}

function candidate(index: number, imported = false) {
  return {
    filename: `img_${index}.png`,
    index,
    seed: 100 + index,
    size_bytes: 1024,
    imported,
    url: `/api/generate/runs/7/candidates/img_${index}.png`,
  }
}

function detail(candidates = [candidate(1), candidate(2), candidate(3, true)]) {
  return {
    run: {
      id: 7,
      model_key: 'qwen_image_edit',
      prompt: '白色连衣裙',
      negative: null,
      status: 'done',
      total: 3,
      done: 3,
      imported_count: 1,
      files_missing: false,
      created_at: '2026-11-01T00:00:00Z',
    },
    candidates,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
  api.fetchGenerateRun.mockResolvedValue(detail())
})

afterEach(() => {
  jest.restoreAllMocks()
})

async function renderReady() {
  render(<GenerateRunScreen />)
  await waitFor(() => expect(screen.getByTestId('candidate-1')).toBeTruthy())
}

/** 取最后一次 Alert */
function lastAlert(): [string, string] {
  const calls = (Alert.alert as jest.Mock).mock.calls
  const call = calls[calls.length - 1] as [string, string]
  return [call[0], call[1]]
}

describe('勾选入库', () => {
  it('进入选择模式后点候选为勾选（不再打开全屏），入库所选并展示结果', async () => {
    api.confirmGenerateCandidates.mockResolvedValue({
      imported: 2,
      skipped: 0,
      ids: ['a', 'b'],
      skipped_items: [],
    })
    await renderReady()

    fireEvent.press(screen.getByTestId('toggle-select'))
    expect(screen.getByText('已选 0 张')).toBeTruthy()

    fireEvent.press(screen.getByTestId('candidate-1'))
    fireEvent.press(screen.getByTestId('candidate-2'))
    expect(screen.getByText('已选 2 张')).toBeTruthy()

    fireEvent.press(screen.getByTestId('confirm-import'))

    await waitFor(() =>
      expect(api.confirmGenerateCandidates).toHaveBeenCalledWith(7, [
        'img_1.png',
        'img_2.png',
      ]),
    )
    await waitFor(() => expect(lastAlert()).toEqual(['已入库', '入库 2 张']))
    // 入库后刷新批次，并退出选择模式
    await waitFor(() => expect(api.fetchGenerateRun).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByText(/已选/)).toBeNull())
  })

  it('已入库的候选在选择模式下不能勾选', async () => {
    await renderReady()

    fireEvent.press(screen.getByTestId('toggle-select'))
    fireEvent.press(screen.getByTestId('candidate-3'))

    expect(screen.getByText('已选 0 张')).toBeTruthy()
  })

  it('跳过项展示原因（内容重复 / 文件不存在）', async () => {
    api.confirmGenerateCandidates.mockResolvedValue({
      imported: 1,
      skipped: 1,
      ids: ['a'],
      skipped_items: [{ filename: 'img_2.png', reason: '已存在（内容重复）' }],
    })
    await renderReady()

    fireEvent.press(screen.getByTestId('toggle-select'))
    fireEvent.press(screen.getByTestId('candidate-1'))
    fireEvent.press(screen.getByTestId('candidate-2'))
    fireEvent.press(screen.getByTestId('confirm-import'))

    await waitFor(() => expect(lastAlert()).toEqual(['已入库', '入库 1 张\n跳过 1 张：已存在（内容重复）']))
  })

  it('未勾选时入库按钮不可用', async () => {
    await renderReady()
    fireEvent.press(screen.getByTestId('toggle-select'))

    expect(screen.getByTestId('confirm-import').props.accessibilityState?.disabled).toBe(true)
  })

  it('入库失败透传后端原因（如批次已丢弃）', async () => {
    api.confirmGenerateCandidates.mockRejectedValue({
      response: { status: 400, data: { detail: '本批已丢弃，候选图已清理，无法入库' } },
    })
    await renderReady()

    fireEvent.press(screen.getByTestId('toggle-select'))
    fireEvent.press(screen.getByTestId('candidate-1'))
    fireEvent.press(screen.getByTestId('confirm-import'))

    await waitFor(() =>
      expect(lastAlert()).toEqual(['入库失败', '本批已丢弃，候选图已清理，无法入库']),
    )
  })
})

describe('丢弃整批', () => {
  it('二次确认后调丢弃接口并返回上一页', async () => {
    api.discardGenerateRun.mockResolvedValue(undefined)
    await renderReady()

    fireEvent.press(screen.getByTestId('discard-run'))

    const call = (Alert.alert as jest.Mock).mock.calls.at(-1) as [
      string,
      string,
      { text: string; onPress?: () => void }[],
    ]
    expect(call[0]).toBe('丢弃整批')
    expect(call[1]).toContain('已入库的素材不受影响')
    const confirm = call[2].find((b) => b.text === '丢弃')

    await act(async () => {
      confirm?.onPress?.()
    })

    await waitFor(() => expect(api.discardGenerateRun).toHaveBeenCalledWith(7))
    await waitFor(() => expect(mockRouter.back).toHaveBeenCalled())
  })

  it('丢弃失败时给后端原因，不返回上一页', async () => {
    api.discardGenerateRun.mockRejectedValue({
      response: { status: 400, data: { detail: '本批仍在生成中：请先取消，等任务停止后再丢弃' } },
    })
    await renderReady()

    fireEvent.press(screen.getByTestId('discard-run'))
    const call = (Alert.alert as jest.Mock).mock.calls.at(-1) as [
      string,
      string,
      { text: string; onPress?: () => void }[],
    ]
    const confirm = call[2].find((b) => b.text === '丢弃')
    await act(async () => {
      confirm?.onPress?.()
    })

    await waitFor(() =>
      expect(lastAlert()).toEqual([
        '丢弃失败',
        '本批仍在生成中：请先取消，等任务停止后再丢弃',
      ]),
    )
    expect(mockRouter.back).not.toHaveBeenCalled()
  })
})
