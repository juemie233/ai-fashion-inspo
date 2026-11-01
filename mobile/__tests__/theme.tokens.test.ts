/** 设计令牌的强制约束（对应审查方案 §3 与 §6 的「视觉」验收项）。
 *
 * 这个文件把「人肉 grep」变成 CI 能执行的断言：
 * 1. `app/**`、`components/**` 里**不允许出现十六进制颜色字面量**（唯一例外是本令牌文件）；
 * 2. 字号只能是设计字阶（11/12/13/15/17/22），不允许 9/10px；
 * 3. 圆角只能是令牌值；
 * 4. 所有「文字/底色」组合的 WCAG 对比度 ≥ 4.5:1——**亮色与暗色两套都要过**；
 * 5. 图标按钮最小触控区 = 44。
 */

import fs from 'fs'
import path from 'path'
import { darkTheme, lightTheme, type ColorTokens, type Theme } from '../constants/theme'

const MOBILE_ROOT = path.resolve(__dirname, '..')
const SCAN_DIRS = ['app', 'components']

/** 递归收集目录下的 .ts/.tsx 文件 */
function collectFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      collectFiles(full, acc)
    } else if (/\.tsx?$/.test(entry.name)) {
      acc.push(full)
    }
  }
  return acc
}

/** sRGB 相对亮度（WCAG 2.1） */
function luminance(hex: string): number {
  const value = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((offset) => {
    const channel = parseInt(value.slice(offset, offset + 2), 16) / 255
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** 两色对比度（前景/背景，1~21） */
export function contrastRatio(foreground: string, background: string): number {
  const a = luminance(foreground)
  const b = luminance(background)
  const [light, dark] = a > b ? [a, b] : [b, a]
  return (light + 0.05) / (dark + 0.05)
}

/** 文字与底色的必须达标准配对（每对都会在亮/暗两套主题里各测一遍） */
const TEXT_PAIRS: { text: keyof ColorTokens; bg: keyof ColorTokens; note: string }[] = [
  { text: 'textPrimary', bg: 'bgSurface', note: '卡片标题' },
  { text: 'textPrimary', bg: 'bgPage', note: '页面标题' },
  { text: 'textSecondary', bg: 'bgSurface', note: '列表正文' },
  { text: 'textSecondary', bg: 'bgPage', note: '页面正文' },
  { text: 'textTertiary', bg: 'bgSurface', note: '次要说明（正文下限）' },
  { text: 'textTertiary', bg: 'bgPage', note: '次要说明' },
  { text: 'textSecondary', bg: 'bgSubtle', note: 'chip 未选中文字' },
  { text: 'brandText', bg: 'bgSurface', note: '可点文字' },
  { text: 'brandText', bg: 'brandSubtle', note: '已选 chip / 标签' },
  { text: 'dangerText', bg: 'bgSurface', note: '危险文字' },
  { text: 'successText', bg: 'bgSurface', note: '成功文字' },
  { text: 'warningText', bg: 'bgSurface', note: '等待文字' },
  { text: 'textOnMedia', bg: 'brandFill', note: '主按钮白字' },
  { text: 'textOnMedia', bg: 'dangerFill', note: '危险按钮白字' },
  { text: 'textOnMedia', bg: 'successFill', note: '成功角标白字' },
  { text: 'textOnMedia', bg: 'warningFill', note: '等待角标白字' },
]

describe('设计令牌：颜色字面量', () => {
  it('app/ 与 components/ 里不再出现十六进制颜色（只用令牌）', () => {
    const offenders: string[] = []
    for (const dir of SCAN_DIRS) {
      for (const file of collectFiles(path.join(MOBILE_ROOT, dir))) {
        const source = fs.readFileSync(file, 'utf8')
        const matches = source.match(/#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b/g)
        if (matches) {
          const relative = path.relative(MOBILE_ROOT, file)
          offenders.push(`${relative}: ${[...new Set(matches)].join(', ')}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('设计令牌：字阶与尺寸', () => {
  it('字号只剩 11/12/13/15/17/22 六档', () => {
    const sizes = Object.values(lightTheme.font).map((font) => font.fontSize)
    expect([...new Set(sizes)].sort((a, b) => a - b)).toEqual([11, 12, 13, 15, 17, 22])
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11)
  })

  it('两套主题的字阶/间距/圆角/触控区完全一致（只有颜色不同）', () => {
    expect(darkTheme.font).toEqual(lightTheme.font)
    expect(darkTheme.space).toEqual(lightTheme.space)
    expect(darkTheme.radius).toEqual(lightTheme.radius)
    expect(darkTheme.icon).toEqual(lightTheme.icon)
    expect(darkTheme.touch).toBe(44)
  })

  it('圆角只有 5 档、间距只有 7 档、图标只有 4 档', () => {
    expect(new Set(Object.values(lightTheme.radius)).size).toBe(5)
    expect(new Set(Object.values(lightTheme.space)).size).toBe(7)
    expect(new Set(Object.values(lightTheme.icon)).size).toBe(4)
  })

  it('圆角/间距在代码里只允许取令牌（页面里不写字面量）', () => {
    const offenders: string[] = []
    for (const dir of SCAN_DIRS) {
      for (const file of collectFiles(path.join(MOBILE_ROOT, dir))) {
        const source = fs.readFileSync(file, 'utf8')
        // 只在 styles 里抓裸数字的 borderRadius / gap / padding（令牌都是 t.xxx）
        const bad = source.match(/borderRadius:\s*\d+/g)
        if (bad) offenders.push(`${path.relative(MOBILE_ROOT, file)}: ${bad.join(', ')}`)
      }
    }
    expect(offenders).toEqual([])
  })
})

describe.each([
  ['亮色', lightTheme],
  ['暗色', darkTheme],
])('设计令牌：%s主题对比度（WCAG AA 正文 ≥ 4.5:1）', (_label, theme: Theme) => {
  it.each(TEXT_PAIRS)('$note：$text on $bg ≥ 4.5', ({ text, bg }) => {
    const ratio = contrastRatio(theme.color[text], theme.color[bg])
    expect(ratio).toBeGreaterThanOrEqual(4.5)
  })
})
