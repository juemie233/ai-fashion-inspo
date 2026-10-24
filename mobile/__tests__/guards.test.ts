/** 字段兜底单测：后端脏数据不能把页面搞崩。
 *
 * 对应 2026-10-24 的畸形数据扫描结果 —— 修之前下列输入都会抛错并卸载整棵树。
 */

import { getFileUrl } from '../services/api'
import { sourceLabel } from '../utils/sourceLabel'

describe('sourceLabel 兜底', () => {
  it('已知来源返回中文', () => {
    expect(sourceLabel('generated')).toBe('AI 生成')
    expect(sourceLabel('douyin')).toBe('抖音')
  })

  it('未知字符串原样返回（便于发现新来源）', () => {
    expect(sourceLabel('brand_new_source')).toBe('brand_new_source')
  })

  it('非字符串一律给「未知来源」，绝不把对象塞进 <Text>', () => {
    expect(sourceLabel(null)).toBe('未知来源')
    expect(sourceLabel(undefined)).toBe('未知来源')
    expect(sourceLabel({})).toBe('未知来源')
    expect(sourceLabel(['x'])).toBe('未知来源')
    expect(sourceLabel(123)).toBe('未知来源')
  })
})

describe('getFileUrl 兜底', () => {
  it('正常相对路径按段编码', () => {
    expect(getFileUrl('http://h:18888', 'images/2026-10/a b.webp')).toBe(
      'http://h:18888/api/files/images/2026-10/a%20b.webp',
    )
  })

  it('空/非字符串路径退化为空路径，不抛 split 错误', () => {
    expect(getFileUrl('http://h:18888', null)).toBe('http://h:18888/api/files/')
    expect(getFileUrl('http://h:18888', [])).toBe('http://h:18888/api/files/')
    expect(getFileUrl('http://h:18888', {})).toBe('http://h:18888/api/files/')
  })
})
