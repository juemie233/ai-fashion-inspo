/** usePageTitle：运行时标题（数据加载后才知道的实体名）的挂载写入与响应式更新。 */

import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { usePageTitle } from '../usePageTitle'
import { APP_NAME } from '@/utils/pageTitle'

/** 用一个最小组件承载 composable（它只写 document.title，不需要渲染内容） */
function mountWithTitle(segment: () => string | null | undefined) {
  const Comp = defineComponent({
    setup() {
      usePageTitle(segment)
      return () => h('div')
    },
  })
  return mount(Comp)
}

describe('usePageTitle', () => {
  afterEach(() => {
    document.title = ''
  })

  it('挂载即写入标题，源变化后自动更新（人物名加载完成前后）', async () => {
    const name = ref<string | null>(null)
    mountWithTitle(() => (name.value ? `人物管理-${name.value}` : '人物管理'))

    expect(document.title).toBe(`人物管理 - ${APP_NAME}`)

    name.value = 'Fox_'
    await nextTick()
    expect(document.title).toBe(`人物管理-Fox_ - ${APP_NAME}`)

    name.value = '多多'
    await nextTick()
    expect(document.title).toBe(`人物管理-多多 - ${APP_NAME}`)
  })

  it('空段落回退站点名', () => {
    mountWithTitle(() => '')
    expect(document.title).toBe(APP_NAME)
  })
})
