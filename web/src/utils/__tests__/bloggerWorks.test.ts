/**
 * 「下载所有作品」判据与提交参数的单测。
 *
 * 这个按钮的可用性同时取决于「人物够不够格」与「f2 通道是否就绪」，前者抽成纯函数
 * 就是为了能在这里断言——该按钮所在的详情页用 Arco 卡片 + 瀑布流，测试环境挂不出来。
 */

import { describe, expect, it } from 'vitest'
import {
  canDownloadWorks,
  canResolveProfile,
  downloadWorksDisabledReason,
  worksDownloadOptions,
} from '../bloggerWorks'

const DOUYIN_URL = 'https://www.douyin.com/user/MS4wLjABAAAAtang'

describe('canDownloadWorks', () => {
  it('抖音博主 + 有主页链接 → 可下载', () => {
    expect(canDownloadWorks({ platform: 'douyin', profile_url: DOUYIN_URL })).toBe(true)
  })

  it('小红书博主不可下载（作品下载走 f2 的抖音通道）', () => {
    expect(
      canDownloadWorks({ platform: 'xiaohongshu', profile_url: 'https://www.xiaohongshu.com/x' }),
    ).toBe(false)
  })

  it('抖音博主但没有主页链接 → 不可下载（f2 无从定位账号）', () => {
    expect(canDownloadWorks({ platform: 'douyin', profile_url: null })).toBe(false)
    expect(canDownloadWorks({ platform: 'douyin', profile_url: '' })).toBe(false)
    // 只有空白字符同样视为没有
    expect(canDownloadWorks({ platform: 'douyin', profile_url: '   ' })).toBe(false)
  })
})

describe('downloadWorksDisabledReason', () => {
  it('可用时返回空串（按钮不加提示）', () => {
    expect(downloadWorksDisabledReason({ platform: 'douyin', profile_url: DOUYIN_URL })).toBe('')
  })

  it('非抖音说明只支持抖音；缺主页链接提示先补链接', () => {
    expect(downloadWorksDisabledReason({ platform: 'xiaohongshu' })).toContain('仅支持抖音')
    expect(downloadWorksDisabledReason({ platform: 'douyin', profile_url: null })).toContain(
      '主页链接',
    )
  })

  it('缺链接但能自动解析时不给出禁用原因（按钮可用，改由确认框说明先解析）', () => {
    expect(downloadWorksDisabledReason({ platform: 'douyin', profile_url: null }, true)).toBe('')
    // 非抖音即使能「解析」也不放行
    expect(downloadWorksDisabledReason({ platform: 'xiaohongshu' }, true)).toContain('仅支持抖音')
  })
})

describe('canResolveProfile', () => {
  it('抖音 + 没主页链接 + f2 可用 → 可自动解析', () => {
    expect(canResolveProfile({ platform: 'douyin', profile_url: null }, true)).toBe(true)
    expect(canResolveProfile({ platform: 'douyin', profile_url: '  ' }, true)).toBe(true)
  })

  it('f2 状态未知（还没回来 / 后端较旧）先放行，由后端给出准确理由', () => {
    expect(canResolveProfile({ platform: 'douyin', profile_url: null }, undefined)).toBe(true)
  })

  it('f2 通道明确不可用时不放行（解析与下载都走 f2）', () => {
    expect(canResolveProfile({ platform: 'douyin', profile_url: null }, false)).toBe(false)
  })

  it('已有主页链接 / 非抖音 → 不需要解析', () => {
    expect(canResolveProfile({ platform: 'douyin', profile_url: DOUYIN_URL }, true)).toBe(false)
    expect(canResolveProfile({ platform: 'xiaohongshu', profile_url: null }, true)).toBe(false)
  })
})

describe('worksDownloadOptions', () => {
  it('点名主页链接、post 模式、先下载后入库；**不传 since_days**', () => {
    const options = worksDownloadOptions(DOUYIN_URL)
    expect(options).toEqual({
      fetch: true,
      mode: 'post',
      profiles: [DOUYIN_URL],
      make_thumbnails: true,
    })
    // 不带日期窗口：后端口径是「首次点名自动 -i all 翻全量，之后增量」，
    // 前端写死 since_days=0 会让每次点击都翻全量（f2 每页固定等一次 timeout）
    expect(options).not.toHaveProperty('since_days')
  })

  it('链接两侧空白会被去掉（避免把带空格的链接当成另一个账号）', () => {
    expect(worksDownloadOptions(`  ${DOUYIN_URL}  `).profiles).toEqual([DOUYIN_URL])
  })
})
