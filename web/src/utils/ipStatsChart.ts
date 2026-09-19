/**
 * 博主 IP 属地统计图的纯函数：把后端返回的分组数据转成 ECharts 配置与图表高度。
 *
 * 为什么单独抽出来（deep module）：图表配置里有两个容易被忽略、但用户一眼就能看出来的
 * 规则，抽成纯函数后可以直接用真实数据渲染断言：
 *
 * 1. **类目轴自动隐藏标签**：ECharts 的 `axisLabel.interval` 默认是 `'auto'`——类目
 *    放不下时会**隔一个藏一个**。320px 高度塞 28 个类目时实测只画出 14 个名字，而画出来
 *    的是从底部（人数最少的）开始数的偶数位，**排名第一的地区直接被藏掉了**。所以这里
 *    固定 `interval: 0`（全部显示），并由 :func:`ipStatsChartHeight` 按类目数把容器撑高，
 *    保证标签放得下、不重叠。
 * 2. **横向柱状图的顺序**：y 轴类目轴 index 0 画在最底部，所以要先 `reverse()` 才能让
 *    人数最多的排在最上面（后端按数量降序返回）。
 */

import type { EChartsOption } from 'echarts'
import type { PersonIpStats } from '@/api/persons'

/** 每个类目占的高度（px）：类目名 12px 字号 + 上下留白，24 足够不重叠 */
export const IP_STATS_ROW_HEIGHT = 24

/** 图表最小高度（px）：类目很少时也不至于挤成一条 */
export const IP_STATS_MIN_HEIGHT = 240

/** 柱条最大宽度（px） */
const BAR_MAX_WIDTH = 18

/** 图表主色（与统计卡片一致） */
const BAR_COLOR = '#18a058'

/**
 * 按类目数算图表高度：每类目 {@link IP_STATS_ROW_HEIGHT}px，最少 {@link IP_STATS_MIN_HEIGHT}px。
 *
 * 接口最多返回 30 条（limit 上限），因此最高约 720px——宁可卡片高一点，也不能让
 * ECharts 把标签藏起来（那正是「排名第一的没显示」的成因）。
 *
 * @param count 类目数
 */
export function ipStatsChartHeight(count: number): number {
  return Math.max(IP_STATS_MIN_HEIGHT, count * IP_STATS_ROW_HEIGHT)
}

/**
 * 构造 IP 属地分布的横向柱状图配置。
 *
 * @param items 后端返回的 `{ip_location, count}` 列表（按人数降序）
 * @returns ECharts 配置；无数据返回 null（图表组件显示空态）
 */
export function buildIpStatsChartOption(
  items: PersonIpStats['items'] | null | undefined,
): EChartsOption | null {
  const rows = items ?? []
  if (rows.length === 0) return null
  // 类目轴 index 0 在最底部：倒序后「人数最多」落在最上方
  const ordered = [...rows].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: ordered.map((i) => i.ip_location),
      // 全部显示：默认 'auto' 会隔一个藏一个，把排名第一的地区也藏掉
      axisLabel: { interval: 0 },
    },
    series: [
      {
        type: 'bar',
        data: ordered.map((i) => i.count),
        barMaxWidth: BAR_MAX_WIDTH,
        itemStyle: { color: BAR_COLOR, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right' },
      },
    ],
  }
}
