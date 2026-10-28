/** M5：出图记录只读（批次列表 + 候选图 + 全屏预览）与 URL 导入。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '111' }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  Stack: ({ children }: { children?: unknown }) => children ?? null,
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    // 档位名由后端提供：测试里给空表，走内置兜底（避免真实请求干扰用例）
    fetchGenerateModelLabels: jest.fn().mockResolvedValue({}),
    fetchGenerateRuns: jest.fn(),
    fetchGenerateRun: jest.fn(),
  }
})

import GenerateScreen from '../app/generate'
import GenerateRunScreen from '../app/generate/[id]'
import { findBareText } from '../test-utils/noBareText'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchGenerateRuns: jest.Mock
  fetchGenerateRun: jest.Mock
}

const RUN = {
  id: 111,
  kind: 'i2i',
  model_key: 'qwen_2_1_uc_i2i',
  status: 'done',
  prompt: '去掉模特穿着的衣服和鞋子，保留袜子',
  negative: '鞋子, 高跟鞋',
  total: 4,
  done: 4,
  failed: 0,
  imported_count: 1,
  files_missing: false,
  created_at: '2026-10-28T01:23:38Z',
}

describe('GenerateScreen（批次列表）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchGenerateRuns.mockResolvedValue({ items: [RUN], total: 89, page: 1, size: 20 })
  })

  it('渲染批次：模型中文名、状态、张数、已入库数，无裸字符串', async () => {
    const utils = render(<GenerateScreen />)
    await waitFor(() => expect(screen.getByText('Qwen 2.1 图生图（未审查）')).toBeTruthy())

    expect(screen.getByText('已完成')).toBeTruthy()
    expect(screen.getByText('4/4 张 · 已入库 1')).toBeTruthy()
    expect(screen.getByText('共 89 个批次')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('未知模型 key 原样显示，缺描述给占位', async () => {
    api.fetchGenerateRuns.mockResolvedValue({
      items: [{ ...RUN, model_key: 'brand_new_model', prompt: '' }],
      total: 1,
      page: 1,
      size: 20,
    })

    render(<GenerateScreen />)
    await waitFor(() => expect(screen.getByText('brand_new_model')).toBeTruthy())
    expect(screen.getByText('（无描述）')).toBeTruthy()
  })

  it('加载失败显示错误文案与空态引导', async () => {
    api.fetchGenerateRuns.mockRejectedValue(new Error('boom'))

    render(<GenerateScreen />)
    await waitFor(() => expect(screen.getByText('加载失败，请检查网络或后端服务')).toBeTruthy())
  })
})

describe('GenerateRunScreen（候选图）', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchGenerateRun.mockResolvedValue({
      run: RUN,
      candidates: [
        {
          filename: '01_seed-1.png',
          index: 1,
          seed: 1,
          size_bytes: 1000,
          imported: true,
          url: '/api/generate/runs/111/candidates/01_seed-1.png',
        },
        {
          filename: '02_seed-2.png',
          index: 2,
          seed: 2,
          size_bytes: 1000,
          imported: false,
          url: '/api/generate/runs/111/candidates/02_seed-2.png',
        },
      ],
    })
  })

  it('渲染批次信息与候选图，已入库的带标记', async () => {
    render(<GenerateRunScreen />)
    await waitFor(() => expect(screen.getByText('Qwen 2.1 图生图（未审查）')).toBeTruthy())

    expect(screen.getByText('去掉模特穿着的衣服和鞋子，保留袜子')).toBeTruthy()
    expect(screen.getByText('负面词：鞋子, 高跟鞋')).toBeTruthy()
    expect(screen.getByText('已入库')).toBeTruthy()
    expect(api.fetchGenerateRun).toHaveBeenCalledWith(111)
  })

  it('点候选图打开全屏预览', async () => {
    render(<GenerateRunScreen />)
    await waitFor(() => expect(screen.getByTestId('candidate-1')).toBeTruthy())

    fireEvent.press(screen.getByTestId('candidate-1'))
    await waitFor(() => expect(screen.getByText('点击任意处关闭')).toBeTruthy())
  })

  it('空批次显示占位文案', async () => {
    api.fetchGenerateRun.mockResolvedValue({ run: RUN, candidates: [] })
    render(<GenerateRunScreen />)

    await waitFor(() => expect(screen.getByText('这个批次没有候选图')).toBeTruthy())
  })
})
