/** 前后端 schema 握手：前端期望的 schema 版本。

  由 vite.config.ts 在 dev 启动 / build 时调用后端 compute_schema_version()
  自动注入为全局常量 __SCHEMA_VERSION__，无需手工同步。改后端 db_migrations
  或 API_CONTRACT_VERSION 后，重启前端 dev / 重新 build 即自动对齐。
  计算失败时为工具串，前端据此跳过版本校验。 */
declare const __SCHEMA_VERSION__: string

export const EXPECTED_SCHEMA_VERSION: string = __SCHEMA_VERSION__
