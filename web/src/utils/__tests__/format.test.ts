/** 格式化工具函数单测。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  formatBytes,
  formatDate,
  formatMs,
  formatSize,
  fmtSize,
  normalizeModelName,
  renderTimeCell,
  shortenText,
  smartSize,
} from '../format'

describe('formatBytes', () => {
  it('零与非法输入', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(NaN)).toBe('0 B')
  })

  it('各数量级', () => {
    expect(formatBytes(512)).toBe('512.0 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('formatSize', () => {
  it('字节与各数量级', () => {
    expect(formatSize(100)).toBe('100 B')
    expect(formatSize(2048)).toBe('2 KB')
    expect(formatSize(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatSize(2 * 1024 ** 3)).toBe('2.00 GB')
  })
})

describe('smartSize / fmtSize', () => {
  it('返回数值与单位', () => {
    expect(smartSize(512)).toEqual({ value: '512', unit: 'B' })
    expect(smartSize(2048)).toEqual({ value: '2.0', unit: 'KB' })
    expect(fmtSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})

describe('formatMs', () => {
  it('null 与各量级', () => {
    expect(formatMs(null)).toBe('-')
    expect(formatMs(500)).toBe('500ms')
    expect(formatMs(2500)).toBe('2.5s')
  })
})

describe('formatDate', () => {
  it('非法输入回退', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate('not-a-date')).toBe('-')
  })

  it('合法日期', () => {
    expect(formatDate('2026-08-16T10:00:00Z')).toContain('2026')
  })
})

describe('normalizeModelName', () => {
  it('无 tag 补 :latest', () => {
    expect(normalizeModelName('all-minilm')).toBe('all-minilm:latest')
  })

  it('已有 tag 保持不变', () => {
    expect(normalizeModelName('all-minilm:latest')).toBe('all-minilm:latest')
    expect(normalizeModelName('qwen3-vl:8b-instruct')).toBe('qwen3-vl:8b-instruct')
  })

  it('空字符串原样返回', () => {
    expect(normalizeModelName('')).toBe('')
  })
})

describe('shortenText', () => {
  it('空文本返回空串', () => {
    expect(shortenText('')).toBe('')
    expect(shortenText('   ')).toBe('')
  })

  it('短文本原样返回（去首尾空白）', () => {
    expect(shortenText(' 图片为手机的截图演示图 ')).toBe('图片为手机的截图演示图')
  })

  it('超长文本截断并加省略号，保留开头含义', () => {
    const long = '图片中人物被遮挡在橱窗或海报图中，无法判断完整穿搭'
    const out = shortenText(long)
    expect(out.length).toBeLessThan(long.length)
    expect(out.endsWith('…')).toBe(true)
    expect(out.startsWith('图片中人物被遮挡')).toBe(true)
  })

  it('自定义阈值', () => {
    expect(shortenText('一二三四五六七八九十', 5)).toBe('一二三四五…')
  })
})

describe('renderTimeCell', () => {
  /** 渲染 renderTimeCell 并返回 DOM 包装（验证真实渲染结果而非 vnode 结构） */
  function renderCell(
    text: string | null | undefined,
    extra?: Parameters<typeof renderTimeCell>[1],
  ) {
    return mount({ render: () => renderTimeCell(text, extra) })
  }

  it('渲染为单行 span，文本为传入时间', () => {
    const wrapper = renderCell('2026-08-16 10:00:00')
    expect(wrapper.element.tagName).toBe('SPAN')
    expect(wrapper.element.style.whiteSpace).toBe('nowrap')
    expect(wrapper.text()).toBe('2026-08-16 10:00:00')
    wrapper.unmount()
  })

  it('空值显示 -；extra.style 与 nowrap 合并', () => {
    const wrapper = renderCell(null, { style: 'color: red' })
    expect(wrapper.text()).toBe('-')
    expect(wrapper.element.style.whiteSpace).toBe('nowrap')
    expect(wrapper.element.style.color).toBe('red')
    wrapper.unmount()
  })

  it('extra 以对象形式提供时同样保留 nowrap', () => {
    const wrapper = renderCell('2026-08-16', { style: { color: 'red' } })
    expect(wrapper.element.style.whiteSpace).toBe('nowrap')
    expect(wrapper.element.style.color).toBe('red')
    wrapper.unmount()
  })
})
