/** 待审核候选 tab：人物名字 → 人物详情页的回归用例。
 *
 * 关注两件事：
 * ① 穿搭博主/职业模特的名字都是**新标签页**链接，指向 /persons/:id?kind=blogger|model
 *    （kind 决定详情页按博主还是模特呈现，与 FaceDetectionSection 同口径）；
 * ② 名字改成 <a> 之后，行的展开点击（.person-head）没有被破坏。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FacePendingTab from '../FacePendingTab.vue'
import type { PersonAggregateItem } from '@/api/faceScan'

const blogger: PersonAggregateItem = {
  person_type: 'blogger',
  person_id: 12,
  name: '小A',
  avatar_path: null,
  count: 3,
  best_conf: 0.91,
}

const model: PersonAggregateItem = {
  person_type: 'model',
  person_id: 34,
  name: '小B',
  avatar_path: null,
  count: 1,
  best_conf: 0.8,
}

function mountTab(persons: PersonAggregateItem[]) {
  return mount(FacePendingTab, {
    props: {
      persons,
      loading: false,
      page: 1,
      total: persons.length,
      detailKey: '',
      detailItems: [],
      detailPage: 1,
      detailTotal: 0,
      detailLoading: false,
      detailChecked: new Set<number>(),
      actionBusy: false,
      gridColumns: 6,
    },
  })
}

describe('FacePendingTab 名字跳转人物详情', () => {
  it('博主与模特的名字都指向新标签页的人物详情，kind 随类型', () => {
    const links = mountTab([blogger, model]).findAll('.person-name-link')
    expect(links).toHaveLength(2)

    expect(links[0].text()).toBe('小A')
    expect(links[0].attributes('href')).toBe('/persons/12?kind=blogger')
    expect(links[0].attributes('target')).toBe('_blank')

    expect(links[1].attributes('href')).toBe('/persons/34?kind=model')
    expect(links[1].attributes('target')).toBe('_blank')
  })

  it('名字仍是行内元素，点整行照旧展开候选明细', async () => {
    const wrapper = mountTab([blogger])
    await wrapper.find('.person-head').trigger('click')
    expect(wrapper.emitted('toggleDetail')).toHaveLength(1)
  })
})
