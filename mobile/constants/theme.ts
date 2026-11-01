/** 设计令牌：颜色 / 字阶 / 间距 / 圆角 / 图标尺寸的**唯一真源**。
 *
 * 规矩（见 docs/移动端UI设计审查与优化方案.md §3）：
 * - `app/**` 与 `components/**` 里**禁止再出现十六进制颜色字面量**，一律引用本文件；
 *   由 `__tests__/theme.tokens.test.ts` 扫描文件系统强制执行。
 * - 字号只允许 11/12/13/15/17/22 六档（`font` 里的 7 个变体，15 出现两次）。
 * - 圆角只用 `radius`、间距只用 `space`、图标尺寸只用 `icon`。
 * - 正文对比度下限 4.5:1（WCAG AA）：`textTertiary` 是**正文最低档**，
 *   `textDisabled` 只能用于图标与装饰，不允许承载文字。
 *
 * 数据来源：审查报告 §1.1 的实测值——原主色 `#6366F1` 配白字只有 4.47:1（差 0.03 不达标），
 * 所以主色换成 indigo-600 `#4F46E5`（6.29:1）；正文灰从 `#9CA3AF`（2.43:1）提到 `#6B7280`（4.83:1）。
 */

/** 主题模式 */
export type ThemeMode = 'light' | 'dark'

/** 状态色调（Badge / 图标 / 文字共用一套语义） */
export type StatusTone = 'neutral' | 'brand' | 'success' | 'danger' | 'warning' | 'onMedia'

/** 颜色令牌 */
export interface ColorTokens {
  /** 主色实心底（按钮底、选中底）—— 白字放上去 ≥ 6.29:1 */
  readonly brandFill: string
  /** 主色文字/图标（浅底上的可点元素） */
  readonly brandText: string
  /** 主色浅底（chip、标签、选中背景） */
  readonly brandSubtle: string
  /** 主色按下态 */
  readonly brandPressed: string
  /** 图片上的主色遮罩（多选态） */
  readonly brandOverlay: string

  readonly textPrimary: string
  readonly textSecondary: string
  /** 正文下限（4.83:1），不要再浅 */
  readonly textTertiary: string
  /** 仅图标/装饰，禁止承载正文 */
  readonly textDisabled: string
  /** 图片/色块上的白字 */
  readonly textOnMedia: string

  readonly border: string
  readonly divider: string

  readonly bgPage: string
  readonly bgSurface: string
  /** 次级面（输入框底、骨架块、chip 底） */
  readonly bgSubtle: string

  /** 实心状态底（角标/进度）：白字压上去 ≥ 4.5:1，两套主题共用 */
  readonly dangerFill: string
  readonly successFill: string
  readonly warningFill: string
  /** 状态文字色（浅底上的文字）：暗色下需要更亮，白字规则不适用 */
  readonly dangerText: string
  readonly successText: string
  readonly warningText: string

  /** 阴影色（仅 Toast/浮层用，§3.3） */
  readonly shadow: string
  /** 弹层遮罩 */
  readonly scrim: string
  /** 卡片底部渐变遮罩的终点色 */
  readonly mediaGradientEnd: string
  /** 视频播放器底色 */
  readonly videoBackdrop: string
}

/** 字体变体：字号 / 行高 / 字重三件套 */
export interface FontToken {
  readonly fontSize: number
  readonly lineHeight: number
  readonly fontWeight: '400' | '500' | '600'
}

/** 主题对象 */
export interface Theme {
  readonly mode: ThemeMode
  readonly color: ColorTokens
  readonly font: {
    /** 页面标题（自绘页头） */
    readonly display: FontToken
    /** 区块标题、弹层标题 */
    readonly title: FontToken
    /** 卡片标题、列表主文本 */
    readonly subtitle: FontToken
    /** 正文、输入框 */
    readonly body: FontToken
    /** 列表次要信息、标签 chip */
    readonly bodySm: FontToken
    /** 说明、meta */
    readonly caption: FontToken
    /** 角标、图片上的文字（**下限**，不允许更小） */
    readonly micro: FontToken
  }
  readonly space: {
    readonly xs: number
    readonly sm: number
    readonly md: number
    readonly lg: number
    readonly xl: number
    readonly xxl: number
    readonly xxxl: number
  }
  readonly radius: {
    readonly badge: number
    readonly control: number
    readonly card: number
    readonly sheet: number
    readonly pill: number
  }
  readonly icon: {
    readonly sm: number
    readonly md: number
    readonly lg: number
    readonly xl: number
  }
  /** 图标按钮的最小触控区（iOS HIG 44 / Material 48 取下限） */
  readonly touch: number
}

/** 字阶（与平台无关，两套主题共用） */
const FONT: Theme['font'] = {
  display: { fontSize: 22, lineHeight: 28, fontWeight: '600' },
  title: { fontSize: 17, lineHeight: 24, fontWeight: '600' },
  subtitle: { fontSize: 15, lineHeight: 22, fontWeight: '600' },
  body: { fontSize: 15, lineHeight: 22, fontWeight: '400' },
  bodySm: { fontSize: 13, lineHeight: 19, fontWeight: '400' },
  caption: { fontSize: 12, lineHeight: 17, fontWeight: '400' },
  micro: { fontSize: 11, lineHeight: 15, fontWeight: '500' },
}

const SPACE: Theme['space'] = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32 }

const RADIUS: Theme['radius'] = { badge: 6, control: 10, card: 14, sheet: 20, pill: 999 }

const ICON: Theme['icon'] = { sm: 16, md: 20, lg: 24, xl: 32 }

/** 亮色主题 */
export const lightTheme: Theme = {
  mode: 'light',
  color: {
    brandFill: '#4F46E5',
    brandText: '#4F46E5',
    brandSubtle: '#EEF2FF',
    brandPressed: '#4338CA',
    brandOverlay: 'rgba(79,70,229,0.16)',

    textPrimary: '#111827',
    textSecondary: '#4B5563',
    textTertiary: '#6B7280',
    textDisabled: '#9CA3AF',
    textOnMedia: '#FFFFFF',

    border: '#E5E7EB',
    divider: '#F1F2F4',

    bgPage: '#F7F8FA',
    bgSurface: '#FFFFFF',
    bgSubtle: '#F3F4F6',

    dangerFill: '#DC2626',
    successFill: '#15803D',
    warningFill: '#B45309',
    dangerText: '#DC2626',
    successText: '#15803D',
    warningText: '#B45309',

    shadow: '#000000',
    scrim: 'rgba(17,24,39,0.55)',
    mediaGradientEnd: 'rgba(17,24,39,0.70)',
    videoBackdrop: '#000000',
  },
  font: FONT,
  space: SPACE,
  radius: RADIUS,
  icon: ICON,
  touch: 44,
}

/** 暗色主题（阶段 4 启用：`app.json` userInterfaceStyle=automatic）
 *
 * 取值原则：底色压到底（`#0F1115`），面板提亮一档（`#171A21`），
 * 主色文字提亮到 `#A5B4FC`（8.9:1），但**实心按钮仍用 `#4F46E5`**（白字 6.29:1 两套主题一致）。
 */
export const darkTheme: Theme = {
  mode: 'dark',
  color: {
    brandFill: '#4F46E5',
    brandText: '#A5B4FC',
    brandSubtle: '#1E2235',
    brandPressed: '#4338CA',
    brandOverlay: 'rgba(165,180,252,0.22)',

    textPrimary: '#F3F4F6',
    textSecondary: '#C7CCD6',
    textTertiary: '#A0A7B4',
    // 暗色下 #6B7280 只有约 3.6:1，提一档才够 4.5:1
    textDisabled: '#6B7280',
    textOnMedia: '#FFFFFF',

    border: '#2C313C',
    divider: '#232833',

    bgPage: '#0F1115',
    bgSurface: '#171A21',
    bgSubtle: '#1F232D',

    dangerFill: '#DC2626',
    successFill: '#15803D',
    warningFill: '#B45309',
    dangerText: '#F87171',
    successText: '#4ADE80',
    warningText: '#FBBF24',

    shadow: '#000000',
    scrim: 'rgba(0,0,0,0.60)',
    mediaGradientEnd: 'rgba(0,0,0,0.78)',
    videoBackdrop: '#000000',
  },
  font: FONT,
  space: SPACE,
  radius: RADIUS,
  icon: ICON,
  touch: 44,
}

/** 预览图占位色（拿不到 dominant_colors 时的兜底） */
export const DEFAULT_MEDIA_BG = lightTheme.color.bgSubtle
