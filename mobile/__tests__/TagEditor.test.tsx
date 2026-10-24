/** 标签编辑弹层：筛选、勾选、移除、新建、保存载荷，以及「2 万+ 标签不卡死」的约束。 */

import { fireEvent, render, screen } from '@testing-library/react-native'
import TagEditor from '../components/TagEditor'
import { findBareText } from '../test-utils/noBareText'
import type { InspirationTag, TagCategoryGroup } from '../services/api'

const GROUPS: TagCategoryGroup[] = [
  {
    category: 'style',
    tags: [
      { id: 1, name: '甜美', category: 'style' },
      { id: 2, name: '通勤', category: 'style' },
    ],
  },
  {
    category: 'color',
    tags: [{ id: 3, name: '白色', category: 'color' }],
  },
] as TagCategoryGroup[]

/** 当前素材已有「甜美」 */
const CURRENT: InspirationTag[] = [
  { tag: { id: 1, name: '甜美', category: 'style' }, confidence: 0.9 },
] as InspirationTag[]

function renderEditor(over: Partial<React.ComponentProps<typeof TagEditor>> = {}) {
  const props = {
    visible: true,
    loading: false,
    groups: GROUPS,
    current: CURRENT,
    onClose: jest.fn(),
    onAdd: jest.fn(),
    onRemove: jest.fn(),
    ...over,
  }
  const utils = render(<TagEditor {...props} />)
  return { props, utils }
}

describe('TagEditor', () => {
  it('渲染标签库与计数提示，且树里没有裸字符串', () => {
    const { utils } = renderEditor()

    expect(screen.getByText('编辑标签')).toBeTruthy()
    expect(screen.getByText('共 3 个标签，输入关键词缩小范围')).toBeTruthy()
    expect(screen.getByText('通勤')).toBeTruthy()
    expect(screen.getByText('白色')).toBeTruthy()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('搜索只保留匹配的标签，并提示匹配数量', () => {
    renderEditor()
    fireEvent.changeText(screen.getByPlaceholderText('搜索或输入新标签'), '通')

    expect(screen.getByText('通勤')).toBeTruthy()
    expect(screen.queryByText('白色')).toBeNull()
    expect(screen.getByText('匹配 1 个')).toBeTruthy()
  })

  it('已关联标签单独成区，点它触发移除（而不是加入待添加）', () => {
    const { props } = renderEditor()
    expect(screen.getByText('已关联（点击移除）')).toBeTruthy()

    // 「甜美」同时出现在已关联区与列表行里，点第一个即已关联 chip
    fireEvent.press(screen.getAllByText('甜美')[0])

    expect(props.onRemove).toHaveBeenCalledWith(1)
    expect(props.onAdd).not.toHaveBeenCalled()
  })

  it('勾选新标签 → 保存时把待添加名称传给 onAdd', () => {
    const { props } = renderEditor()
    fireEvent.press(screen.getByText('通勤'))
    expect(screen.getByText('待添加 1 个')).toBeTruthy()

    fireEvent.press(screen.getByText('保存'))
    expect(props.onAdd).toHaveBeenCalledWith(['通勤'])
  })

  it('未勾选任何标签时保存按钮不触发 onAdd', () => {
    const { props } = renderEditor()
    fireEvent.press(screen.getByText('保存'))

    expect(props.onAdd).not.toHaveBeenCalled()
  })

  it('搜索无匹配时提供新建入口，点击即用该名字新增', () => {
    const { props } = renderEditor()
    fireEvent.changeText(screen.getByPlaceholderText('搜索或输入新标签'), '复古风')

    fireEvent.press(screen.getByText('新建标签「复古风」'))
    expect(props.onAdd).toHaveBeenCalledWith(['复古风'])
  })

  it('loading 时显示占位文案而不是空列表', () => {
    renderEditor({ loading: true })
    expect(screen.getByText('正在加载标签…')).toBeTruthy()
  })

  it('标签库有 5000 个时不会一次性渲染全部（防止真机 ANR）', () => {
    // 真实库里有 22751 个标签，全量渲染 chip 墙会让 JS 线程卡死（2026-10-24 真机 ANR）
    const huge: TagCategoryGroup[] = [
      {
        category: 'body_part',
        tags: Array.from({ length: 5000 }, (_, i) => ({
          id: i + 100,
          name: `标签${i}`,
          category: 'body_part',
        })),
      },
    ] as TagCategoryGroup[]

    renderEditor({ groups: huge, current: [] })

    expect(screen.getByText('共 5000 个标签，输入关键词缩小范围')).toBeTruthy()
    const rendered = screen.getAllByText(/^标签\d+$/)
    // 虚拟列表 + 默认 60 条上限：渲染量必须远小于 5000
    expect(rendered.length).toBeLessThanOrEqual(60)
    expect(rendered.length).toBeGreaterThan(0)
  })
})
