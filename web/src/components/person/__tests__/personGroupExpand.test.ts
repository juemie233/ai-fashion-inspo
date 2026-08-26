/** 人物列表「人物组展开」回归测试：验证 Arco Table 受控展开的事件绑定方式。
 *
 * 背景：之前用 :on-expanded-change（v-bind）绑定展开回调，Arco 内部
 * emit("expandedChange") 查找 camelCase 的 onExpandedChange prop 匹配不上，
 * 点击加号后回调不触发、行永远不展开。必须用 @expanded-change 事件语法。
 *
 * 本测试模拟 PersonListSection 的完整链路（row-key="id" + 受控
 * expandedRowKeys + @expanded-change 回调写回受控数组 + #expand-row 插槽），
 * 点击加号后断言：回调触发、受控数组更新、展开行渲染、再点折叠。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { Table } from '@arco-design/web-vue'

const rows = [
  { id: 1, name: '独立博主A', group_members: [] },
  { id: 2, name: '多多', group_members: [{ id: 3, name: 'Fox_' }] },
]

/** 完整模拟 PersonListSection：展开回调把 keys 写回受控数组 expandedGroupIds */
function makeWrapper() {
  const expandedGroupIds = ref<number[]>([])
  const calls: string[] = []
  const Comp = defineComponent({
    components: { ATable: Table },
    setup() {
      return { rows, expandedGroupIds }
    },
    methods: {
      handleExpandedChange(keys: Array<string | number>) {
        calls.push(JSON.stringify(keys))
        expandedGroupIds.value = keys.map(Number)
      },
    },
    template: `
      <a-table
        :columns="[{ title: '人物', dataIndex: 'name' }]"
        :data="rows"
        row-key="id"
        :expandable="{ expandedRowKeys: expandedGroupIds }"
        @expanded-change="handleExpandedChange"
      >
        <template #expand-row="{ record }">
          <div data-test="expand-content">
            <span v-if="record.group_members && record.group_members.length">组员: {{ record.group_members.map(function(m) { return m.name }).join(',') }}</span>
            <span v-else>无同组账号</span>
          </div>
        </template>
      </a-table>
    `,
  })
  return { wrapper: mount(Comp, { attachTo: document.body }), expandedGroupIds, calls }
}

describe('Arco Table 受控展开（人物组回归）', () => {
  it('点击有组人物的加号：回调触发 + 受控数组更新 + 展开行显示组员', async () => {
    const { wrapper, expandedGroupIds, calls } = makeWrapper()

    const btns = wrapper.findAll('.arco-table-expand-btn')
    expect(btns.length).toBe(2)

    // 点击第二行（多多，有组员 Fox_）的展开按钮
    await btns[1].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // 回调必须触发（这是之前 :on-expanded-change 绑定下丢失的事件）
    expect(calls).toEqual(['[2]'])
    expect(expandedGroupIds.value).toEqual([2])

    const content = wrapper.find('[data-test="expand-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('组员: Fox_')

    wrapper.unmount()
  })

  it('点击独立博主的加号：展开行显示「无同组账号」', async () => {
    const { wrapper, calls } = makeWrapper()

    const btns = wrapper.findAll('.arco-table-expand-btn')
    await btns[0].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(calls).toEqual(['[1]'])

    const content = wrapper.find('[data-test="expand-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('无同组账号')

    wrapper.unmount()
  })

  it('再次点击已展开行：回调传回空数组（折叠）', async () => {
    const { wrapper, calls } = makeWrapper()

    const btns = wrapper.findAll('.arco-table-expand-btn')
    await btns[0].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(calls).toEqual(['[1]'])

    // 再次点击折叠
    await btns[0].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(calls).toEqual(['[1]', '[]'])

    wrapper.unmount()
  })
})
