/** 共享类型定义：标签相关。 */

import type { TagCategory } from './inspiration'

/** 标签对象 */
export interface Tag {
  id: number
  name: string
  category: TagCategory
  created_at?: string
  usage_count?: number
}

/** 标签类别分组 */
export interface TagCategoryGroup {
  category: TagCategory
  tags: Tag[]
}
