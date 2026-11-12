/** 分享入库的结果归纳（P1）：判重识别、文案归纳、标题提取。 */

import {
  buildShareSummary,
  describeShareError,
  extractShareTitle,
  isDuplicateError,
  MAX_SHARE_FILES,
} from '../utils/shareIntent'

describe('isDuplicateError', () => {
  it('409 视为「已在库中」（后端内容去重）', () => {
    expect(isDuplicateError({ response: { status: 409 } })).toBe(true)
    expect(
      isDuplicateError({ response: { status: 409, data: { detail: '该素材已存在（内容重复）' } } }),
    ).toBe(true)
  })

  it('没有 status 但 detail 明确说了「已存在」也算', () => {
    expect(isDuplicateError({ response: { data: { detail: '该素材已存在（平台 ID 冲突）' } } })).toBe(
      true,
    )
    expect(isDuplicateError({ response: { data: { detail: '已在库中' } } })).toBe(true)
  })

  it('其它错误（网络/400/500）不算重复', () => {
    expect(isDuplicateError(new Error('network'))).toBe(false)
    expect(isDuplicateError({ response: { status: 400 } })).toBe(false)
    expect(isDuplicateError({ response: { status: 500, data: { detail: '内部错误' } } })).toBe(false)
    expect(isDuplicateError(null)).toBe(false)
  })
})

describe('describeShareError', () => {
  it('优先后端 detail（业务 4xx 的原因要原样给用户）', () => {
    expect(
      describeShareError({ response: { status: 400, data: { detail: '不支持的文件类型: video/webm' } } }),
    ).toBe('不支持的文件类型: video/webm')
  })

  it('没有 detail 时给通用可操作提示', () => {
    expect(describeShareError(new Error('network'))).toBe('请检查后端地址与网络后重试')
    expect(describeShareError({ response: { data: { detail: '   ' } } })).toBe(
      '请检查后端地址与网络后重试',
    )
  })
})

describe('buildShareSummary', () => {
  it('全部成功（图片）：标题「已入库」+ 一句说明', () => {
    expect(buildShareSummary('image', { created: 3, duplicate: 0, failed: 0 })).toEqual({
      title: '已入库',
      message: '已入库 3 张，AI 将自动分析标签',
    })
  })

  it('全部成功（链接）：标题「已导入」，措辞用「个链接」', () => {
    expect(buildShareSummary('url', { created: 1, duplicate: 0, failed: 0 })).toEqual({
      title: '已导入',
      message: '已导入 1 个链接',
    })
  })

  it('全部重复：标题「已在库中」，不提失败', () => {
    const summary = buildShareSummary('image', { created: 0, duplicate: 2, failed: 0 })
    expect(summary.title).toBe('已在库中')
    expect(summary.message).toBe('2 张已在库中（内容重复），已跳过')
  })

  it('部分成功 + 部分重复 + 部分失败：标题给中性汇总，明细逐行', () => {
    const summary = buildShareSummary('image', {
      created: 2,
      duplicate: 1,
      failed: 1,
      firstError: '网络超时',
    })
    expect(summary.title).toBe('分享入库结果')
    expect(summary.message.split('\n')).toEqual([
      '已入库 2 张，AI 将自动分析标签',
      '1 张已在库中（内容重复），已跳过',
      '1 张失败：网络超时',
    ])
  })

  it('全部失败：标题「入库失败」，并带原因', () => {
    const summary = buildShareSummary('url', {
      created: 0,
      duplicate: 0,
      failed: 1,
      firstError: '笔记链接暂不支持',
    })
    expect(summary.title).toBe('入库失败')
    expect(summary.message).toBe('1 个链接失败：笔记链接暂不支持')
  })

  it('超出上限的部分单独说明', () => {
    const summary = buildShareSummary('image', {
      created: MAX_SHARE_FILES,
      duplicate: 0,
      failed: 0,
      skipped: 3,
    })
    expect(summary.title).toBe('分享入库结果')
    expect(summary.message).toContain(`另有 3 张超出单次上限（${MAX_SHARE_FILES}），未处理`)
  })

  it('失败但缺少原因时给兜底文案', () => {
    expect(buildShareSummary('image', { created: 0, duplicate: 0, failed: 2 }).message).toBe(
      '2 张失败：请稍后重试',
    )
  })
})

describe('extractShareTitle', () => {
  it('去掉链接与口令前缀，取出标题', () => {
    const text = '7.88 复制打开抖音，看看【夏日穿搭】这套真好看 https://v.douyin.com/abc/'
    expect(extractShareTitle(text, 'https://v.douyin.com/abc/')).toBe('夏日穿搭 这套真好看')
  })

  it('没有链接时也能取（不再截断 URL 之外的字符）', () => {
    expect(extractShareTitle('分享一条穿搭笔记', null)).toBe('分享一条穿搭笔记')
  })

  it('最多 24 字，空值返回空串', () => {
    const long = '这是一条非常非常长的分享文案用来验证截断逻辑是否正常工作'
    expect(extractShareTitle(long, null).length).toBe(24)
    expect(extractShareTitle(null, null)).toBe('')
    expect(extractShareTitle(undefined, null)).toBe('')
  })
})
