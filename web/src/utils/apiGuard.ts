/**
 * API 响应运行时校验：关键字段类型核对，捕捉后端字段缺失/类型漂移导致的静默异常。
 *
 * 背景：此前「检测并匹配返回缺 matched_blogger_name → 前端回退显示博主 #id」、
 * 「队列统计 unanalyzed 恒为 0 → 按钮误禁用」等问题的共同根因是前端直接信任
 * 后端响应结构，字段缺失时无任何提示。本工具在关键接口处做轻量形状校验，
 * **只告警不阻断**（记录 console.error，返回原数据），让问题在运行时可见、
 * 可定位，同时不因校验引入新的功能回归。
 */

/** 字段形状描述：字段名 -> 期望的 JS 类型（支持 ? 后缀表示可空，如 'number?' = number | null） */
export type FieldShape = Record<
  string,
  | 'number'
  | 'string'
  | 'boolean'
  | 'object'
  | 'array'
  | `${'number' | 'string' | 'boolean' | 'object' | 'array'}?`
>

function typeOf(v: unknown, type: FieldShape[string]): boolean {
  const nullable = type.endsWith('?')
  const base = nullable ? type.slice(0, -1) : type
  if (nullable && v === null) return true
  if (base === 'array') return Array.isArray(v)
  if (base === 'object') return typeof v === 'object' && v !== null
  return typeof v === base
}

function describe(v: unknown): string {
  if (v === undefined) return '缺失'
  if (v === null) return 'null'
  if (Array.isArray(v)) return 'array'
  return typeof v
}

/** 校验单个对象的关键字段（只告警不阻断），返回原数据 */
export function warnShape<T>(data: unknown, shape: FieldShape, label: string): T {
  if (data === null || typeof data !== 'object') {
    console.error(`[apiGuard] ${label}: 响应不是对象（实际 ${describe(data)}）`)
    return data as T
  }
  const obj = data as Record<string, unknown>
  for (const [field, type] of Object.entries(shape)) {
    if (!typeOf(obj[field], type)) {
      console.error(`[apiGuard] ${label}: 字段 ${field} 期望 ${type}，实际 ${describe(obj[field])}`)
    }
  }
  return data as T
}

/** 校验数组元素的关键字段（只告警不阻断），返回原数组 */
export function warnItems<T>(data: unknown, shape: FieldShape, label: string): T[] {
  if (!Array.isArray(data)) {
    console.error(`[apiGuard] ${label}: 期望数组（实际 ${describe(data)}）`)
    return data as T[]
  }
  for (const item of data) {
    if (item === null || typeof item !== 'object') {
      console.error(`[apiGuard] ${label}: 数组元素不是对象`)
      continue
    }
    const obj = item as Record<string, unknown>
    for (const [field, type] of Object.entries(shape)) {
      if (!typeOf(obj[field], type)) {
        console.error(
          `[apiGuard] ${label}: 元素字段 ${field} 期望 ${type}，实际 ${describe(obj[field])}`,
        )
      }
    }
  }
  return data as T[]
}
