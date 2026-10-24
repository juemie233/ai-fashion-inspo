/** 页面渲染安全性测试：确保没有「裸字符串」直接挂到非 <Text> 节点上。
 *
 * 这是对 2026-10-24 真机白屏事故的回归防线：
 *   Text strings must be rendered within a <Text> component.
 * 该错误会让整棵组件树被卸载（用户看到白屏），而且只有**原生** RN 会拦，
 * react-native-web 不校验，所以 Web 预览与普通渲染测试都发现不了。
 */

import { render } from '@testing-library/react-native'
import { findBareText } from '../test-utils/noBareText'
import GalleryScreen from '../app/(tabs)/index'
import SearchScreen from '../app/(tabs)/search'
import SettingsScreen from '../app/(tabs)/settings'
import { useInspirationStore, type Inspiration } from '../hooks/useInspirations'

/** 后端 /api/inspirations 的条目形状 */
const BASE_ITEM: Record<string, unknown> = {
  id: 'i1',
  source_type: 'douyin',
  source_url: null,
  source_author: null,
  source_platform_id: null,
  file_path: 'images/2026-10/a.webp',
  thumbnail_path: 'thumbnails/2026-10/thumb_a.jpg',
  media_type: 'image',
  dominant_colors: null,
  is_favorite: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
  tags: [],
  analysis_status: 'done',
}

/** 造一条素材；故意放宽类型 —— 这里要测的正是「后端返回不干净数据」的场景 */
function makeItem(over: Record<string, unknown> = {}): Inspiration {
  return { ...BASE_ITEM, ...over } as unknown as Inspiration
}

/** 渲染后断言：树里不存在裸字符串 */
function expectNoBareText(component: ReturnType<typeof render>): void {
  const tree = component.toJSON()
  const hits = findBareText(tree)
  if (hits.length) {
    const detail = hits.map((h) => `  ${h.parentType} ← ${JSON.stringify(h.text)} @ ${h.path}`)
    throw new Error(`发现裸字符串（会被 RN 原生拒绝，真机白屏）:\n${detail.join('\n')}`)
  }
}

beforeEach(() => {
  useInspirationStore.setState({
    items: [],
    total: 0,
    page: 1,
    loading: false,
    apiBaseUrl: 'http://127.0.0.1:18888',
  })
})

describe('灵感库（GalleryScreen）渲染安全', () => {
  it('有素材 + 正常标签时不产生裸字符串', () => {
    useInspirationStore.setState({
      items: [
        makeItem({
          tags: [
            { tag: { id: 1, name: '针织开衫', category: 'item_type' }, confidence: 0.9 },
            { tag: { id: 2, name: '极简', category: 'style' }, confidence: 0.8 },
          ],
        }),
      ],
      total: 1,
    })
    expectNoBareText(render(<GalleryScreen />))
  })

  it('tags 为 null / 空数组时不崩', () => {
    useInspirationStore.setState({
      items: [makeItem({ tags: null }), makeItem({ id: 'i2', tags: [] })],
      total: 2,
    })
    expectNoBareText(render(<GalleryScreen />))
  })

  it('缩略图为空串时回退原图，不产生裸字符串', () => {
    useInspirationStore.setState({
      items: [makeItem({ thumbnail_path: '', tags: [] })],
      total: 1,
    })
    expectNoBareText(render(<GalleryScreen />))
  })

  it('空列表（ListEmptyComponent）不产生裸字符串', () => {
    useInspirationStore.setState({ items: [], total: 0 })
    expectNoBareText(render(<GalleryScreen />))
  })

  it('未收藏/已收藏两种状态都安全', () => {
    useInspirationStore.setState({
      items: [makeItem({ is_favorite: true }), makeItem({ id: 'i3', is_favorite: false })],
      total: 2,
    })
    expectNoBareText(render(<GalleryScreen />))
  })
})

describe('搜索页 / 设置页渲染安全', () => {
  it('搜索页初始状态不产生裸字符串', () => {
    expectNoBareText(render(<SearchScreen />))
  })

  it('设置页不产生裸字符串', () => {
    expectNoBareText(render(<SettingsScreen />))
  })
})
