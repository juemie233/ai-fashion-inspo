/** 统计卡片行：数字/字符串/空值/说明/高亮。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import StatCardGrid from '../StatCardGrid.vue'

type StatItemLike = {
  title: string
  value?: number
  text?: string
  suffix?: string
  valueStyle?: Record<string, string>
  precision?: number
  placeholder?: string
  note?: string
  highlight?: boolean
}

function mountGrid(items: StatItemLike[], props: Record<string, unknown> = {}) {
  return mount(StatCardGrid, {
    props: { items, ...props },
    global: { plugins: [ArcoVue] },
  })
}

describe('StatCardGrid', () => {
  it('数字值走 statistic，字符串值走自绘文本', () => {
    const wrapper = mountGrid([
      { title: '总数', value: 12, suffix: '条' },
      { title: '体积', text: '1.2 GB' },
    ])

    expect(wrapper.text()).toContain('总数')
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('条')
    expect(wrapper.text()).toContain('体积')
    expect(wrapper.text()).toContain('1.2 GB')
    wrapper.unmount()
  })

  it('空字符串值显示占位符，说明与高亮样式生效', () => {
    const wrapper = mountGrid([
      {
        title: '缺失',
        text: '',
        placeholder: '-',
        note: '说明文案',
        highlight: true,
        valueStyle: { color: 'red' },
      },
    ])

    expect(wrapper.find('.stat-custom-value').text()).toBe('-')
    expect(wrapper.find('.stat-custom-value').attributes('style')).toContain('color: red')
    expect(wrapper.find('.stat-note').text()).toBe('说明文案')
    expect(wrapper.find('.arco-card').attributes('style')).toContain('border-color')
    wrapper.unmount()
  })

  it('span / marginBottom 透传到栅格', () => {
    const wrapper = mountGrid([{ title: 'A', value: 1 }], {
      span: 8,
      marginBottom: '24px',
    })

    expect(wrapper.find('.arco-row').attributes('style')).toContain('24px')
    wrapper.unmount()
  })
})
