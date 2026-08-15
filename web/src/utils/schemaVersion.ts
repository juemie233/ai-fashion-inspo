/** 前后端 schema 握手：前端期望的 schema 版本。

  必须与后端 app/db_migrations.py 的 compute_schema_version() 返回值保持一致。
  后端修改数据库结构（_SCHEMA_COLUMNS / _SCHEMA_INDEXES）或 API 契约版本
  （API_CONTRACT_VERSION）后，需同步更新此常量，否则前端会提示「版本不一致」。 */
export const EXPECTED_SCHEMA_VERSION = 'e3218fd6-1'
