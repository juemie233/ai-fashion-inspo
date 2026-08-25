/** 标签相关的跨组件类型定义。 */

/** 标签基础结构：跨场景复用（健康度/聚类/网络图/层级树/重复对等）。
 *
 * 各面板特有的字段（source、parent_id、degree 等）通过 extends 扩展。 */
export interface TagBrief {
  id: number
  name: string
  category: string
  usage_count: number
}

/** 疑似重复对的单侧标签（同步扫描接口不返回 usage_count，故设为可选） */
export interface TagDuplicateSide {
  id: number
  name: string
  category: string
  usage_count?: number
}

/** 疑似重复标签对：标签管理页重复扫描与高级管理健康度明细共用此结构。
 *  tag_a/tag_b 在健康度明细中可能因标签已删除而为 null。 */
export interface TagDuplicatePair {
  tag_a: TagDuplicateSide | null
  tag_b: TagDuplicateSide | null
  similarity: number
}
