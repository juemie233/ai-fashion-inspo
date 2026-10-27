import { formatCount, formatAge } from '../format'

/** 格式化工具函数单测。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatMs,
  formatSize,
  normalizeModelName,
  renderTimeCell,
  shortModelLabel,
  shortenText,
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

describe('shortModelLabel', () => {
  it('去掉全角括号里的说明', () => {
    expect(shortModelLabel('Qwen-Image 2.1（参考图改款）')).toBe('Qwen-Image 2.1')
    expect(shortModelLabel('Z-Image-Turbo（文生图）')).toBe('Z-Image-Turbo')
  })

  it('没有括号时原样返回', () => {
    expect(shortModelLabel('Z-Image-Turbo')).toBe('Z-Image-Turbo')
    expect(shortModelLabel('')).toBe('')
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

describe('formatDateTime', () => {
  it('定宽补零：不受 locale 影响，1 月/个位数时也补零', () => {
    // 本地时区构造，避免 UTC 偏移导致跨天
    const d = new Date(2026, 0, 5, 9, 7, 3)
    expect(formatDateTime(d)).toBe('2026-01-05 09:07')
    expect(formatDateTime(d, true)).toBe('2026-01-05 09:07:03')
  })

  it('接受 ISO 字符串（后端 created_at / trained_at 就是这种）', () => {
    const d = new Date(2026, 9, 20, 14, 30, 0)
    expect(formatDateTime(d.toISOString())).toBe('2026-10-20 14:30')
  })

  it('空值与非法值统一回退 "-"（模板里直接插值不会崩）', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(undefined)).toBe('-')
    expect(formatDateTime('')).toBe('-')
    expect(formatDateTime('not-a-date')).toBe('-')
  })

  it('输出定宽（14 / 19 字符）：时间列不会因宽度变化换行', () => {
    const d = new Date(2026, 11, 31, 23, 59, 58)
    expect(formatDateTime(d)).toHaveLength(16)
    expect(formatDateTime(d, true)).toHaveLength(19)
  })
})

describe('formatCount（资料卡数字紧凑显示）', () => {
  it('万/亿量级压缩，小数值原样', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatCount(5)).toBe('5')
    expect(formatCount(9999)).toBe('9999')
    expect(formatCount(12345)).toBe('1.2万')
    expect(formatCount(123456)).toBe('12.3万')
    expect(formatCount(100000000)).toBe('1亿')
    expect(formatCount(123456789)).toBe('1.2亿')
  })

  it('缺失值显示 -（而不是 0，避免「0 粉丝」的误读）', () => {
    expect(formatCount(null)).toBe('-')
    expect(formatCount(undefined)).toBe('-')
  })
})

describe('formatAge（快照/最近素材的时效提示）', () => {
  it('按分钟/小时/天/月分级', () => {
    const now = Date.now()
    expect(formatAge(new Date(now - 5_000).toISOString())).toBe('刚刚')
    expect(formatAge(new Date(now - 10 * 60_000).toISOString())).toBe('10 分钟前')
    expect(formatAge(new Date(now - 3 * 3600_000).toISOString())).toBe('3 小时前')
    expect(formatAge(new Date(now - 5 * 24 * 3600_000).toISOString())).toBe('5 天前')
    expect(formatAge(new Date(now - 70 * 24 * 3600_000).toISOString())).toBe('2 个月前')
  })

  it('空值与未来时间不炸', () => {
    expect(formatAge(null)).toBe('')
    expect(formatAge('')).toBe('')
    expect(formatAge('not-a-date')).toBe('')
    expect(formatAge(new Date(Date.now() + 60_000).toISOString())).toBe('刚刚')
  })
})
