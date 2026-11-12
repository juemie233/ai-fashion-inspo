/** 合集维护（P0-4）：新建 / 改名 / 上移下移 / 删除。 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native'
import { Alert } from 'react-native'

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    fetchCollections: jest.fn(),
    createCollection: jest.fn(),
    updateCollection: jest.fn(),
    deleteCollection: jest.fn(),
    reorderCollections: jest.fn(),
  }
})

import CollectionsScreen from '../app/collections'
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require('../services/api') as {
  fetchCollections: jest.Mock
  createCollection: jest.Mock
  updateCollection: jest.Mock
  deleteCollection: jest.Mock
  reorderCollections: jest.Mock
}

const LIST = [
  { id: 1, name: '春日穿搭', kind: 'manual', item_count: 5 },
  { id: 2, name: '通勤合集', kind: 'manual', item_count: 3 },
  { id: 3, name: '智能合集', kind: 'smart', item_count: null },
]

beforeEach(() => {
  jest.clearAllMocks()
  api.fetchCollections.mockResolvedValue([...LIST])
  jest.spyOn(Alert, 'alert').mockImplementation(() => {})
})

afterEach(() => {
  jest.restoreAllMocks()
})

async function renderReady() {
  render(<CollectionsScreen />)
  await waitFor(() => expect(screen.getByText('春日穿搭')).toBeTruthy())
}

describe('新建合集', () => {
  it('填写名称后调用创建接口并刷新列表', async () => {
    api.createCollection.mockResolvedValue({ id: 9, name: '夏季度假', kind: 'manual' })
    await renderReady()

    fireEvent.press(screen.getByTestId('collection-create'))
    // 「新建合集」既是工具条按钮又是弹层标题，这里用输入框定位弹层
    await waitFor(() => expect(screen.getByTestId('collection-name-input')).toBeTruthy())

    fireEvent.changeText(screen.getByTestId('collection-name-input'), '夏季度假')
    fireEvent.press(screen.getByTestId('collection-editor-submit'))

    await waitFor(() => expect(api.createCollection).toHaveBeenCalledWith('夏季度假'))
    // 创建后重新拉列表
    await waitFor(() => expect(api.fetchCollections).toHaveBeenCalledTimes(2))
  })

  it('名称为空时提交按钮不可用', async () => {
    await renderReady()
    fireEvent.press(screen.getByTestId('collection-create'))
    await waitFor(() => expect(screen.getByTestId('collection-editor-submit')).toBeTruthy())

    const submit = screen.getByTestId('collection-editor-submit')
    expect(submit.props.accessibilityState?.disabled).toBe(true)
  })

  it('同名冲突给出可读提示', async () => {
    api.createCollection.mockRejectedValue({ response: { status: 409 } })
    await renderReady()

    fireEvent.press(screen.getByTestId('collection-create'))
    fireEvent.changeText(screen.getByTestId('collection-name-input'), '春日穿搭')
    fireEvent.press(screen.getByTestId('collection-editor-submit'))

    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith('新建失败', '同名合集已存在，换个名字试试'),
    )
  })
})

describe('改名', () => {
  it('长按卡片打开操作弹层，改名后调用更新接口', async () => {
    api.updateCollection.mockResolvedValue({ id: 1, name: '春日通勤', kind: 'manual' })
    await renderReady()

    fireEvent(screen.getByTestId('collection-1'), 'longPress')
    await waitFor(() => expect(screen.getByText('删除合集')).toBeTruthy())

    fireEvent.press(screen.getByTestId('collection-rename'))
    await waitFor(() => expect(screen.getByText('合集改名')).toBeTruthy())
    // 输入框预填原名
    expect(screen.getByTestId('collection-name-input').props.value).toBe('春日穿搭')

    fireEvent.changeText(screen.getByTestId('collection-name-input'), '春日通勤')
    fireEvent.press(screen.getByTestId('collection-editor-submit'))

    await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { name: '春日通勤' }))
  })
})

describe('上移 / 下移', () => {
  it('中间项可上移，提交完整的一级合集顺序', async () => {
    api.reorderCollections.mockResolvedValue(undefined)
    await renderReady()

    fireEvent(screen.getByTestId('collection-2'), 'longPress')
    await waitFor(() => expect(screen.getByText('上移')).toBeTruthy())
    fireEvent.press(screen.getByTestId('collection-move-up'))

    await waitFor(() => expect(api.reorderCollections).toHaveBeenCalledWith([2, 1, 3]))
  })

  it('第一项不能再上移、最后一项不能再下移（按钮置灰）', async () => {
    await renderReady()

    fireEvent(screen.getByTestId('collection-1'), 'longPress')
    await waitFor(() => expect(screen.getByText('上移')).toBeTruthy())
    expect(screen.getByTestId('collection-move-up').props.accessibilityState?.disabled).toBe(true)

    fireEvent.press(screen.getByText('取消'))
    fireEvent(screen.getByTestId('collection-3'), 'longPress')
    await waitFor(() => expect(screen.getByText('下移')).toBeTruthy())
    expect(screen.getByTestId('collection-move-down').props.accessibilityState?.disabled).toBe(true)
  })
})

describe('删除合集', () => {
  it('二次确认后调删除接口，并从列表中移除', async () => {
    api.deleteCollection.mockResolvedValue(undefined)
    await renderReady()

    fireEvent(screen.getByTestId('collection-1'), 'longPress')
    await waitFor(() => expect(screen.getByText('删除合集')).toBeTruthy())
    fireEvent.press(screen.getByTestId('collection-delete'))

    const alertCall = (Alert.alert as jest.Mock).mock.calls.at(-1) as
      | [string, string, { text: string; onPress?: () => void }[]]
      | undefined
    expect(alertCall?.[0]).toBe('删除合集')
    // 明确告知素材不会被删除
    expect(alertCall?.[1]).toContain('素材不会被删除')
    const confirm = alertCall?.[2].find((b) => b.text === '删除')
    expect(confirm).toBeTruthy()

    await act(async () => {
      confirm?.onPress?.()
    })

    await waitFor(() => expect(api.deleteCollection).toHaveBeenCalledWith(1))
    await waitFor(() => expect(screen.queryByText('春日穿搭')).toBeNull())
  })
})
