/** 卡片渲染的「畸形数据」扫描：找出会漏出裸字符串的字段形态。
 *
 * 思路：逐个字段灌入不正常的值（null / 空串 / 数字 / 对象 / 数组里塞 null …），
 * 用 noBareText 检查渲染树。命中即说明：后端一旦返回这种形状，真机就会白屏。
 */

import { render } from '@testing-library/react-native'
import { findBareText } from '../test-utils/noBareText'
import GalleryScreen from '../app/(tabs)/index'
import { useInspirationStore } from '../hooks/useInspirations'

const BASE = {
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

/** 每个字段会尝试的畸形值 */
const WEIRD: unknown[] = [undefined, null, '', 0, 'x', [], {}, [null], [{ tag: null }], [{ tag: {} }]]

const FIELDS = [
  'id',
  'source_type',
  'file_path',
  'thumbnail_path',
  'media_type',
  'dominant_colors',
  'is_favorite',
  'created_at',
  'tags',
  'analysis_status',
] as const

interface Finding {
  field: string
  value: unknown
  kind: 'bare-text' | 'throw'
  detail: string
}

const findings: Finding[] = []

describe('卡片畸形数据扫描', () => {
  // 按字段拆成独立用例：每个字段 11 次整屏渲染约 2.4s（CI 2 核上更慢）。
  // 原先合成一个大循环要 24s+（同步循环无法被 jest 超时中断，CI 上直接把
  // jest 步骤拖到 65s），且失败时看不出是哪个字段。
  for (const field of FIELDS) {
    it(`字段 ${field}：${WEIRD.length} 种畸形值都不抛渲染错`, () => {
      for (const value of WEIRD) {
        const item = { ...BASE, [field]: value }
        // 部分畸形值会在渲染中抛 TypeError（也是真机白屏来源，一并记录）
        try {
          useInspirationStore.setState({
            items: [item] as never,
            total: 1,
            page: 1,
            loading: false,
            apiBaseUrl: 'http://127.0.0.1:18888',
          })
          const tree = render(<GalleryScreen />).toJSON()
          const hits = findBareText(tree)
          if (hits.length) {
            findings.push({
              field,
              value,
              kind: 'bare-text',
              detail: hits.map((h) => `${h.parentType} ← ${JSON.stringify(h.text)}`).join('; '),
            })
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          // 只关心「渲染期」错误；act 警告之类不算
          if (!msg.includes('not wrapped in act')) {
            findings.push({ field, value, kind: 'throw', detail: msg.slice(0, 160) })
          }
        }
      }

      const thrown = findings.filter((f) => f.field === field && f.kind === 'throw')
      expect(thrown).toEqual([])
    })
  }

  it('汇总：畸形值没有漏出裸字符串', () => {
    const bare = findings.filter((f) => f.kind === 'bare-text')
    // 把发现打印出来，便于定位（裸字符串会让真机白屏，属于必须修的回归）
    if (bare.length) {
      // eslint-disable-next-line no-console
      console.log('=== 畸形数据发现 ===\n' + JSON.stringify(bare, null, 2))
    } else {
      // eslint-disable-next-line no-console
      console.log('=== 畸形数据扫描：未发现问题 ===')
    }
    expect(bare).toEqual([])
  })
})
