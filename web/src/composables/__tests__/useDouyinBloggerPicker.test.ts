/**
 * useDouyinBloggerPicker 测试：采集页「按博主全量下载」选博主的数据源。
 *
 * 关注四件事：① 候选来自已登记抖音博主（平台过滤 + 按素材数排序）；
 * ② 远程搜索会换掉 options，但**已选中的人仍留在缓存里**（标签与提交不能丢）；
 * ③ 选中但缺主页标识的人会被单独标出来（提交前得先解析）；
 * ④ 自动解析成功后，缓存里的主页链接要立刻可用于提交。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Message } from '@arco-design/web-vue'

import { useDouyinBloggerPicker } from '../useDouyinBloggerPicker'
import type { Person } from '@shared/types/person'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))

vi.mock('@/api/client', () => ({ default: mocks }))

const DOUYIN_URL = 'https://www.douyin.com/user/MS4wLjABAAAAtang'

function blogger(overrides: Partial<Person> = {}): Person {
  return {
    id: 1,
    name: '唐思瑶ya',
    platform: 'douyin',
    profile_url: DOUYIN_URL,
    platform_user_id: 'MS4wLjABAAAAtang',
    inspiration_count: 12,
    ...overrides,
  }
}

function listResponse(items: Person[]) {
  return { data: { items, total: items.length, page: 1, size: 40 } }
}

describe('useDouyinBloggerPicker', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.post.mockReset()
    mocks.put.mockReset()
    mocks.get.mockResolvedValue(listResponse([blogger()]))
  })

  it('搜索：按抖音平台 + 素材数倒序取候选（平铺，不折叠人物组）', async () => {
    const { options, search } = useDouyinBloggerPicker()
    await search('唐思瑶')

    const params = mocks.get.mock.calls[0][1].params
    expect(mocks.get.mock.calls[0][0]).toBe('/bloggers')
    expect(params).toMatchObject({
      platform: 'douyin',
      search: '唐思瑶',
      sort: 'count',
      grouped: false,
    })
    expect(options.value).toHaveLength(1)
  })

  it('候选标签带素材数与「缺主页链接」标记', async () => {
    mocks.get.mockResolvedValue(
      listResponse([
        blogger({ id: 1, name: '有链接的' }),
        blogger({ id: 2, name: '缺链接的', profile_url: null, platform_user_id: null }),
      ]),
    )
    const { selectOptions, search } = useDouyinBloggerPicker()
    await search()

    expect(selectOptions.value[0].label).toBe('有链接的（12 条素材）')
    expect(selectOptions.value[1].label).toContain('缺主页链接')
  })

  it('远程搜索换掉候选后，已选中的人仍在缓存里（提交标识不丢）', async () => {
    const target = blogger({ id: 7, name: '被选中的' })
    mocks.get.mockResolvedValue(listResponse([target]))
    const { search, onChange, selectedKeys, selectedIds } = useDouyinBloggerPicker()
    await search()
    onChange([7])

    // 再搜别的关键字：候选里已经没有她
    mocks.get.mockResolvedValue(listResponse([blogger({ id: 9, name: '别人' })]))
    await search('别人')

    expect(selectedIds.value).toEqual([7])
    expect(selectedKeys.value).toEqual([DOUYIN_URL])
  })

  it('缺主页标识的已选博主被单独标出（提交时会被后端跳过）', async () => {
    mocks.get.mockResolvedValue(
      listResponse([
        blogger({ id: 3, name: '一朵芝士', profile_url: null, platform_user_id: null }),
      ]),
    )
    const { search, onChange, missing } = useDouyinBloggerPicker()
    await search()
    onChange([3])

    expect(missing.value.map((p) => p.name)).toEqual(['一朵芝士'])
  })

  it('自动解析成功：缓存里的主页链接立刻可用于提交', async () => {
    mocks.get.mockResolvedValue(
      listResponse([
        blogger({ id: 3, name: '一朵芝士', profile_url: null, platform_user_id: null }),
      ]),
    )
    mocks.post.mockResolvedValue({
      data: {
        ok: true,
        blogger_id: 3,
        name: '一朵芝士',
        profile_url: 'https://www.douyin.com/user/MS4wLy_resolved',
        platform_user_id: 'MS4wLy_resolved',
        ip_location: '浙江',
        source: 'f2_aweme',
        reason: '',
      },
    })
    const success = vi.spyOn(Message, 'success').mockImplementation((() => {}) as never)
    const { search, onChange, resolveMissing, missing, selectedKeys } = useDouyinBloggerPicker()
    await search()
    onChange([3])

    const ok = await resolveMissing()

    expect(ok).toBe(1)
    expect(mocks.post.mock.calls[0][0]).toBe('/bloggers/3/resolve-douyin-profile')
    expect(missing.value).toEqual([]) // 解析后不再是「缺失」
    expect(selectedKeys.value).toEqual(['https://www.douyin.com/user/MS4wLy_resolved'])
    expect(success).toHaveBeenCalled()
  })

  it('自动解析失败：提示后端原因且不改变提交标识', async () => {
    mocks.get.mockResolvedValue(
      listResponse([
        blogger({ id: 4, name: '没有作品ID', profile_url: null, platform_user_id: null }),
      ]),
    )
    mocks.post.mockResolvedValue({
      data: {
        ok: false,
        blogger_id: 4,
        name: '没有作品ID',
        profile_url: '',
        platform_user_id: '',
        ip_location: '',
        source: '',
        reason: '这位博主的素材里没有可用的抖音作品 ID',
      },
    })
    const warn = vi.spyOn(Message, 'warning').mockImplementation((() => {}) as never)
    const { search, onChange, resolveMissing, selectedKeys } = useDouyinBloggerPicker()
    await search()
    onChange([4])

    const ok = await resolveMissing()

    expect(ok).toBe(0)
    expect(String(warn.mock.calls[0][0])).toContain('没有可用的抖音作品 ID')
    expect(selectedKeys.value).toEqual([])
  })

  it('没有缺标识的人时不发解析请求', async () => {
    const { search, onChange, resolveMissing } = useDouyinBloggerPicker()
    await search()
    onChange([1])

    expect(await resolveMissing()).toBe(0)
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('清空选择后标识为空（提交成功后复位）', async () => {
    const { search, onChange, clear, selectedKeys, selectedIds } = useDouyinBloggerPicker()
    await search()
    onChange([1])

    clear()

    expect(selectedIds.value).toEqual([])
    expect(selectedKeys.value).toEqual([])
  })
})
