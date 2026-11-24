/** 数据完整性卡片：扫描器来源标签（Rust 加速 / Python 回退）。 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import AdminIntegrityCheck from '../AdminIntegrityCheck.vue'

function mountCard(scanner?: string) {
  return mount(AdminIntegrityCheck, {
    props: {
      missingFiles: [],
      orphanFiles: [],
      orphanBytes: 0,
      checking: false,
      scanner,
    },
    global: { plugins: [ArcoVue] },
  })
}

describe('AdminIntegrityCheck', () => {
  it('显示 Rust 加速标签', () => {
    expect(mountCard('rust').text()).toContain('Rust 加速')
  })

  it('显示 Python 回退标签', () => {
    expect(mountCard('python').text()).toContain('Python 扫描')
  })
})
