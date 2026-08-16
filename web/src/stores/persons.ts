/** 人物状态管理：人物列表、加载态、分页与筛选。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchPersons, type Person } from '@/api/persons'
import type { PersonType } from '@shared/types/person'

export const usePersonsStore = defineStore('persons', () => {
  /** 人物列表 */
  const persons = ref<Person[]>([])
  /** 是否正在加载 */
  const loading = ref(false)
  /** 总数（分页用） */
  const total = ref(0)
  /** 当前页码 */
  const page = ref(1)
  /** 每页数量 */
  const size = ref(20)
  /** 当前搜索关键字 */
  const search = ref('')
  /** 内容类型筛选（'' 表示全部） */
  const personType = ref<PersonType | ''>('')
  /** 平台筛选（'' 表示全部） */
  const platform = ref('')
  /** 排序方式：newest | name | count */
  const sort = ref<'newest' | 'name' | 'count'>('newest')

  /** 加载人物列表（force=true 忽略已有数据强制刷新） */
  async function load(force: boolean = false) {
    if (!force && loading.value) return
    loading.value = true
    try {
      const data = await fetchPersons({
        page: page.value,
        size: size.value,
        search: search.value || undefined,
        person_type: personType.value || undefined,
        platform: platform.value || undefined,
        sort: sort.value,
      })
      persons.value = data.items
      total.value = data.total
    } catch (e) {
      console.error('加载人物列表失败', e)
    } finally {
      loading.value = false
    }
  }

  /** 重置到第一页并重新加载（搜索/筛选变化时调用） */
  async function reload() {
    page.value = 1
    await load(true)
  }

  /** 翻页 */
  async function setPage(p: number) {
    page.value = p
    await load(true)
  }

  return {
    persons,
    loading,
    total,
    page,
    size,
    search,
    personType,
    platform,
    sort,
    load,
    reload,
    setPage,
  }
})
