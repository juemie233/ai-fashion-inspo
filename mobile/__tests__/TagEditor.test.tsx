/** 标签编辑弹层：筛选、勾选、标记移除、新建、保存载荷，以及「2 万+ 标签不卡死」的约束。
 *
 * 审查修复 P1-8 后的语义：点击已关联标签只是**标记移除**（可再点取消），
 * 点「保存」才统一提交（`onSubmit({add, remove})`），不再点一下就调接口删除。
 */

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
    onSubmit: jest.fn(),
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

  it('点已关联标签只是「标记移除」，保存时才提交 remove', () => {
    const { props } = renderEditor()
    expect(screen.getByText('已关联（点击标记移除）')).toBeTruthy()

    // 标记后立刻调接口删除是改前的缺陷（误触无法撤销），现在必须先点保存
    fireEvent.press(screen.getByTestId('linked-tag-1'))
    expect(props.onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('待移除 1 个')).toBeTruthy()

    fireEvent.press(screen.getByText('保存'))
    expect(props.onSubmit).toHaveBeenCalledWith({ add: [], remove: [1] })
  })

  it('再点一次已关联标签可以取消标记', () => {
    const { props } = renderEditor()
    fireEvent.press(screen.getByTestId('linked-tag-1'))
    fireEvent.press(screen.getByTestId('linked-tag-1'))

    expect(screen.queryByText('待移除 1 个')).toBeNull()
    // 没有待提交内容时保存按钮不可用
    fireEvent.press(screen.getByText('保存'))
    expect(props.onSubmit).not.toHaveBeenCalled()
  })

  it('勾选新标签 → 保存时把待添加名称传给 onSubmit', () => {
    const { props } = renderEditor()
    fireEvent.press(screen.getByText('通勤'))
    expect(screen.getByText('待添加 1 个')).toBeTruthy()

    fireEvent.press(screen.getByText('保存'))
    expect(props.onSubmit).toHaveBeenCalledWith({ add: ['通勤'], remove: [] })
  })

  it('未勾选任何标签时保存按钮不触发提交', () => {
    const { props } = renderEditor()
    fireEvent.press(screen.getByText('保存'))

    expect(props.onSubmit).not.toHaveBeenCalled()
  })

  it('搜索无匹配时提供新建入口，点击即勾选为待新建', () => {
    const { props } = renderEditor()
    fireEvent.changeText(screen.getByPlaceholderText('搜索或输入新标签'), '复古风')

    fireEvent.press(screen.getByText('新建标签「复古风」'))
    // 点新建只是加入待添加，仍需保存
    expect(screen.getByText('待添加 1 个')).toBeTruthy()
    fireEvent.press(screen.getByText('保存'))
    expect(props.onSubmit).toHaveBeenCalledWith({ add: ['复古风'], remove: [] })
  })

  it('loading 时显示占位文案而不是空列表', () => {
    renderEditor({ loading: true })
    expect(screen.getByText('正在加载标签…')).toBeTruthy()
  })

  it('标签库加载失败时给重试入口', () => {
    const onRetry = jest.fn()
    renderEditor({ loadError: true, groups: [], onRetry })
    fireEvent.press(screen.getByText('重试'))
    expect(onRetry).toHaveBeenCalled()
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
