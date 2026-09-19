/**
 * IP 属地统计图（utils/ipStatsChart）测试：用真实 ECharts（SVG 渲染器）画出配置，
 * 断言**每个类目名都出现在图里**。
 *
 * 回归背景：`yAxis.axisLabel.interval` 默认 `'auto'`，类目放不下时会**隔一个藏一个**——
 * 320px 高度 + 28 个类目时实测只画出 14 个名字，且被藏掉的包含排名第一的「浙江」
 * （用户反馈「排名第一的没显示」）。这里锁死「全部显示 + 高度随类目数增长」。
 */

import { describe, expect, it } from 'vitest'
import { init, use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import {
  IP_STATS_MIN_HEIGHT,
  IP_STATS_ROW_HEIGHT,
  buildIpStatsChartOption,
  ipStatsChartHeight,
} from '../ipStatsChart'

use([BarChart, GridComponent, TooltipComponent, SVGRenderer])

/** 真实库里的分布（降序，与后端返回一致；28 个类目） */
const ITEMS = [
  { ip_location: '浙江', count: 50 },
  { ip_location: '未知', count: 39 },
  { ip_location: '广东', count: 36 },
  { ip_location: '湖北', count: 26 },
  { ip_location: '四川', count: 17 },
  { ip_location: '山东', count: 15 },
  { ip_location: '江苏', count: 15 },
  { ip_location: '福建', count: 13 },
  { ip_location: '上海', count: 12 },
  { ip_location: '安徽', count: 12 },
  { ip_location: '重庆', count: 10 },
  { ip_location: '广西', count: 8 },
  { ip_location: '北京', count: 7 },
  { ip_location: '河北', count: 7 },
  { ip_location: '河南', count: 7 },
  { ip_location: '江西', count: 6 },
  { ip_location: '陕西', count: 6 },
  { ip_location: '湖南', count: 5 },
  { ip_location: '辽宁', count: 5 },
  { ip_location: '山西', count: 4 },
  { ip_location: '天津', count: 3 },
  { ip_location: '美国', count: 2 },
  { ip_location: '贵州', count: 2 },
  { ip_location: '中国香港', count: 1 },
  { ip_location: '云南', count: 1 },
  { ip_location: '新疆', count: 1 },
  { ip_location: '韩国', count: 1 },
  { ip_location: '黑龙江', count: 1 },
]

/** 把配置渲染成 SVG，返回画出来的文本元素内容 */
function renderTexts(items: { ip_location: string; count: number }[]): string[] {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const chart = init(el, undefined, {
    width: 900,
    height: ipStatsChartHeight(items.length),
    renderer: 'svg',
  })
  chart.setOption(buildIpStatsChartOption(items)!, true)
  const texts = [...el.innerHTML.matchAll(/>([^<>]+)<\/text>/g)].map((m) => m[1].trim())
  chart.dispose()
  el.remove()
  return texts
}

describe('buildIpStatsChartOption', () => {
  it('无数据返回 null（图表组件显示空态）', () => {
    expect(buildIpStatsChartOption([])).toBeNull()
    expect(buildIpStatsChartOption(null)).toBeNull()
  })

  it('y 轴类目倒序（人数最多的排最上面）且强制显示全部标签', () => {
    const option = buildIpStatsChartOption(ITEMS)!

    const yAxis = option.yAxis as { data: string[]; axisLabel: { interval: number } }
    expect(yAxis.axisLabel.interval).toBe(0)
    expect(yAxis.data[0]).toBe('黑龙江') // 最底部 = 人数最少
    expect(yAxis.data[yAxis.data.length - 1]).toBe('浙江') // 最顶部 = 人数最多
  })

  it('渲染真实图表：28 个类目名全部画出来（含排名第一的浙江）', () => {
    const texts = renderTexts(ITEMS)

    const missing = ITEMS.map((i) => i.ip_location).filter((n) => !texts.includes(n))
    expect(missing).toEqual([])
    expect(texts).toContain('浙江')
    // 柱条数值标签也在（人数最多的 50）
    expect(texts).toContain('50')
  })

  it('图表高度随类目数增长，保证标签放得下', () => {
    expect(ipStatsChartHeight(0)).toBe(IP_STATS_MIN_HEIGHT)
    expect(ipStatsChartHeight(3)).toBe(IP_STATS_MIN_HEIGHT)
    expect(ipStatsChartHeight(28)).toBe(28 * IP_STATS_ROW_HEIGHT)
    // 后端 limit 上限 30 → 最高 720px，文档化这个上界
    expect(ipStatsChartHeight(30)).toBe(720)
  })
})
