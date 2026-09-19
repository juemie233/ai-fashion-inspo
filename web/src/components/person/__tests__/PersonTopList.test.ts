/**
 * 热门人物排行（PersonTopList）测试：头像必须走 Arco 的 image-url 分支。
 *
 * 回归背景：`a-avatar` 用**插槽 img** 传头像时，Arco 只给 imageUrl 分支的 wrapper 加
 * `arco-avatar-image`，而圆形裁剪（overflow + border-radius）挂在这个类上——插槽 img
 * 既不裁剪也没有尺寸约束，会按原图尺寸渲染成**方形并溢出**容器（用户反馈「头像显示为
 * 方形」）。这里锁死「有头像 → arco-avatar-image」「无头像 → arco-avatar-text 占位」，
 * 以及「先占位、后出现头像」这个动态路径。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { Avatar, Card, Space } from '@arco-design/web-vue'
import PersonTopList from '../PersonTopList.vue'

function makePerson(over: Record<string, unknown> = {}) {
  return {
    id: 7,
    name: '里香',
    platform: 'douyin',
    avatar_path: null,
    inspiration_count: 12,
    ...over,
  }
}

function mountList(persons: Record<string, unknown>[]) {
  return mount(PersonTopList, {
    props: { persons: persons as never },
    // 测试环境不注册 Arco（main.ts 里才全局注册）：这里按需注册用到的组件（kebab 名，
    // 与模板里的 a-xxx 对应），且必须用**真实实现**——本用例验证的正是 Arco 的圆形裁剪类名
    global: { components: { 'a-avatar': Avatar, 'a-card': Card, 'a-space': Space } },
  })
}

describe('PersonTopList 头像显示', () => {
  it('有头像：wrapper 带 arco-avatar-image（圆形裁剪生效）', () => {
    const wrapper = mountList([makePerson({ avatar_path: 'avatars/avatar_7.jpg' })])

    expect(wrapper.find('.arco-avatar-image').exists()).toBe(true)
    expect(wrapper.find('.arco-avatar-text').exists()).toBe(false)
    // 图片由 Arco 渲染（带尺寸约束），不是插槽里的裸 img
    expect(wrapper.find('.arco-avatar-image img').exists()).toBe(true)
  })

  it('无头像：显示人形占位（arco-avatar-text）', () => {
    const wrapper = mountList([makePerson()])

    expect(wrapper.find('.arco-avatar-text').exists()).toBe(true)
    expect(wrapper.find('.arco-avatar-image').exists()).toBe(false)
  })

  it('先无头像、后设了头像：动态切换后仍是 arco-avatar-image（不会掉进方形渲染）', async () => {
    const wrapper = mountList([makePerson()])
    expect(wrapper.find('.arco-avatar-text').exists()).toBe(true)

    await wrapper.setProps({
      persons: [makePerson({ avatar_path: 'avatars/avatar_7.jpg' })] as never,
    })

    expect(wrapper.find('.arco-avatar-image').exists()).toBe(true)
  })
})
