/** 人物模块的**共享工厂**（从 api/persons.ts 抽出，C-5 的前置）。
 *
 * `createPersonApi` 被博主接口与模特接口共用，而这两个模块都在**模块求值时**调用它；
 * 留在任一方都会迫使另一方反向 import，形成求值期循环依赖（运行时 undefined）。故单独成
 * 模块，双方都只依赖这里。
 */

import apiClient from './client'

import type {
  Blogger,
  Model,
  PersonBrief,
  PersonDetail,
  PersonFaceCandidates,
  PersonForm,
  PersonInsights,
  PersonInspirationFilters,
  PersonInspirationsOut,
  PersonIpStats,
  PersonListOut,
  PersonSimilarItem,
  PersonStats,
  PersonWorksOut,
} from './persons'

export function createPersonApi(kind: 'bloggers' | 'models') {
  const base = `/${kind}`

  return {
    /** 获取人物列表（分页 + 搜索 + 平台筛选）。
     *
     * 人脸检测约束：手动指定只允许选择已注册人脸库的人物（确保检测匹配
     * 对象都在特征库内，库外人物不可选）。
     *
     * - face_registered_only=true：仅返回已注册人脸库的人物（用于人脸检测手动选择）
     */
    async fetchList(
      params: {
        page?: number
        size?: number
        search?: string
        platform?: string
        /** 来源筛选（manual 手动 / auto_collect 采集自动登记）：自动登记的账号可一键隐藏 */
        source?: string
        sort?: 'newest' | 'name' | 'count'
        /** 素材数排序方向（sort=count 时生效） */
        order?: 'asc' | 'desc'
        /** 仅返回已注册人脸库的人物（用于人脸检测手动选择） */
        face_registered_only?: boolean
        /** 按人物组折叠（方案 B，仅博主）：同组只返回主账号，组内账号在 group_members */
        grouped?: boolean
      } = {},
    ): Promise<PersonListOut> {
      const { data } = await apiClient.get<PersonListOut>(base, { params })
      return data
    },

    /** 获取人物详情（含素材数与风格画像） */
    async fetchDetail(id: number): Promise<PersonDetail> {
      const { data } = await apiClient.get<PersonDetail>(`${base}/${id}`)
      return data
    },

    /** 数量统计：总数 + 平台分布（人物管理页顶部统计卡片） */
    async fetchStats(): Promise<PersonStats> {
      const { data } = await apiClient.get<PersonStats>(`${base}/stats`)
      return data
    },

    /** 创建人物 */
    async create(body: PersonForm) {
      const { data } = await apiClient.post<Blogger | Model>(base, body)
      return data
    },

    /** 更新人物（部分更新） */
    async update(id: number, body: PersonForm) {
      const { data } = await apiClient.patch<Blogger | Model>(`${base}/${id}`, body)
      return data
    },

    /** 删除人物（仅当其无关联素材时允许；有关联素材时后端返回 400 与提示） */
    async remove(id: number) {
      await apiClient.delete(`${base}/${id}`)
    },

    /** 获取人物的素材列表（分页 + 排序 + 筛选，筛选由后端执行以保证分页分母一致） */
    async fetchInspirations(
      id: number,
      page: number = 1,
      size: number = 20,
      sort?: string,
      filters: PersonInspirationFilters = {},
    ): Promise<PersonInspirationsOut> {
      const { data } = await apiClient.get<PersonInspirationsOut>(`${base}/${id}/inspirations`, {
        params: { page, size, sort, ...filters },
      })
      return data
    },

    /** 按「作品」分组分页（同一抖音作品的 6~9 张图算一组；B1 作品视图） */
    async fetchWorks(id: number, page: number = 1, size: number = 12): Promise<PersonWorksOut> {
      const { data } = await apiClient.get<PersonWorksOut>(`${base}/${id}/works`, {
        params: { page, size },
      })
      return data
    },

    /** 人物洞察：近 12 个月新增 / 质量构成 / 首末素材时间（B3+B4） */
    async fetchInsights(id: number): Promise<PersonInsights> {
      const { data } = await apiClient.get<PersonInsights>(`${base}/${id}/insights`)
      return data
    },

    /** 风格相近人物（标签余弦相似度，共同标签 ≥3 才推荐） */
    async fetchSimilar(
      id: number,
      limit: number = 6,
    ): Promise<{ similar: PersonSimilarItem[]; duplicates: unknown[] }> {
      const { data } = await apiClient.get<{
        similar: PersonSimilarItem[]
        duplicates: unknown[]
      }>(`${base}/${id}/similar`, { params: { limit } })
      return data
    },

    /** 人脸注册候选：已匹配素材（一键注册）+ 未匹配候选脸簇（A3） */
    async fetchFaceCandidates(id: number): Promise<PersonFaceCandidates> {
      const { data } = await apiClient.get<PersonFaceCandidates>(`${base}/${id}/face-candidates`)
      return data
    },

    /** 获取热门人物排行（按素材数倒序） */
    async fetchTop(limit: number = 20): Promise<PersonListOut['items']> {
      const { data } = await apiClient.get<PersonListOut['items']>(`${base}/top`, {
        params: { limit },
      })
      return data
    },

    /** 按 IP 属地分组统计（空属地归「未知」），供人物管理页展示地域分布 */
    async fetchIpStats(limit: number = 30): Promise<PersonIpStats> {
      const { data } = await apiClient.get<PersonIpStats>(`${base}/ip-stats`, {
        params: { limit },
      })
      return data
    },

    /** 按名称模糊匹配人物（用于选择去重） */
    async suggest(name: string): Promise<PersonListOut['items']> {
      const { data } = await apiClient.get<PersonListOut['items']>(`${base}/suggestions`, {
        params: { name },
      })
      return data
    },

    /** 调用本地大模型根据该人物的标签画像生成一段简介（不入库，需手动保存） */
    async generateBio(id: number): Promise<{ bio: string; sources?: string[] }> {
      // sources 说明这段简介参考了什么（tags / signature），详情页据此提示用户
      const { data } = await apiClient.post<{ bio: string; sources?: string[] }>(
        `${base}/${id}/generate-bio`,
      )
      return data
    },

    /** 给素材批量关联人物（幂等） */
    async link(inspirationId: string, personIds: number[]) {
      const { data } = await apiClient.post<{ added: PersonBrief[]; count: number }>(
        `/inspirations/${inspirationId}/${kind}`,
        { person_ids: personIds },
      )
      return data
    },

    /** 解除素材与人物关联 */
    async unlink(inspirationId: string, personId: number) {
      const { data } = await apiClient.delete<{ removed: number }>(
        `/inspirations/${inspirationId}/${kind}/${personId}`,
      )
      return data
    },
  }
}
