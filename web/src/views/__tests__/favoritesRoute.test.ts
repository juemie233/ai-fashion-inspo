/** 收藏中心路由登记：/favorites → FavoritesCenterView，标题「收藏中心」。 */

import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('收藏中心路由', () => {
  it('注册 /favorites，name=favorites，meta.title=收藏中心', () => {
    const route = router.getRoutes().find((r) => r.path === '/favorites')
    expect(route).toBeTruthy()
    expect(route?.name).toBe('favorites')
    expect(route?.meta.title).toBe('收藏中心')
  })

  it('收藏合集路由仍在（两个入口分开）', () => {
    expect(router.getRoutes().some((r) => r.path === '/collections')).toBe(true)
  })
})
