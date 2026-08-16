/** 来源类型中文映射单测。 */

import { describe, expect, it } from 'vitest'
import { sourceLabel, SOURCE_TYPE_LABELS } from '../sourceLabel'

describe('sourceLabel', () => {
  it('全部来源映射为中文', () => {
    expect(sourceLabel('manual_upload')).toBe('手动上传')
    expect(sourceLabel('scraper')).toBe('自动采集')
    expect(sourceLabel('xiaohongshu')).toBe('小红书')
    expect(sourceLabel('douyin')).toBe('抖音')
    expect(sourceLabel('browser_extension')).toBe('浏览器插件')
    expect(sourceLabel('batch_import')).toBe('目录导入')
    expect(sourceLabel('url_import')).toBe('链接导入')
  })

  it('未知来源回退原值', () => {
    expect(sourceLabel('unknown_source')).toBe('unknown_source')
    expect(sourceLabel('')).toBe('')
  })

  it('映射表包含 batch_import 与 url_import（共享类型已扩展）', () => {
    expect(SOURCE_TYPE_LABELS).toHaveProperty('batch_import')
    expect(SOURCE_TYPE_LABELS).toHaveProperty('url_import')
  })
})
