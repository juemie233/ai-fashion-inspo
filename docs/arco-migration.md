# Arco Design 全量迁移方案（Naive UI → Arco Design）

> 状态：试点已验证（`/persons-arco`，用户确认满意），进入全量迁移规划。
> 恢复点：`git checkout pre-arco-pilot`（试点前的完整 Naive 版本）。

## 〇、硬性要求：彻底抛弃 Naive UI

1. 迁移是**单向、不可逆的最终目标**：P3 完成后必须删除 Naive UI 依赖（`naive-ui` 从 `package.json` 移除、`app.use(naive)` 与全局 provider 壳删除、`n-*` 标签与 `useMessage/useDialog/useNotification` 等命令式 API 使用归零）。
2. **任何新代码禁止再引入 Naive UI**（组件、命令式 API、类型引用）；新增功能一律使用 Arco Design。
3. 代码评审中检出 Naive 引用（含注释、示例、文档）即视为不合格，当场要求迁移后再合入。
4. 迁移完成前由人工把关 + `no-restricted-imports` 风格检查防回潮；P3 依赖移除后由构建天然强制（import 直接报错）。
5. 迁移期间可并存（Naive 兜底），但**不新增任何 Naive 用法**，存量 Naive 引用只减不增。

## 一、现状与目标

- 现状：Vue 3 + Naive UI，**88 个文件、约 1050 处组件标签、48 种组件**深度使用（含 `useMessage`/`useDialog` 等命令式 API、`h()` render、主题 provider）。
- 目标：全量替换为 `@arco-design/web-vue`（已安装，试点页验证通过），统一字节系设计语言 + 设计令牌主题定制，解决 UI 观感问题。**最终彻底抛弃 Naive UI（见「〇」）。**
- 试点页：`web/src/views/ArcoPersonPilotView.vue`（路由 `/persons-arco`）——组件/主题/布局已验证，作为迁移样板。

## 二、核心组件映射表

| Naive UI | Arco Design | 备注（API 差异） |
| --- | --- | --- |
| `n-button` | `a-button` | `type` 取值不同：primary/secondary/text/outline；`ghost` → `type="outline"` |
| `n-card` | `a-card` | `title` 插槽 → `#title`；`size="small"` 兼容 |
| `n-data-table` | `a-table` | 差异最大：列定义 `render({record, column, rowIndex})`；分页默认内置，可 `:pagination="false"` 外置 |
| `n-form` / `n-form-item` | `a-form` / `a-form-item` | 校验规则结构不同（`rules: { field: [{required, message}] }`）；`model` 必传 |
| `n-input` | `a-input` | 事件差异：`@keydown.enter` → `@press-enter`；`v-model:value` → `v-model` |
| `n-select` | `a-select` | `v-model:value` → `v-model`；`@update:value` → `@change` |
| `n-modal` | `a-modal` | `preset="card"` → `title` + `footer` 插槽；`v-model:show` → `v-model:visible` |
| `n-popconfirm` | `a-popconfirm` | `@positive-click` → `@ok`；插槽结构不同 |
| `n-message`（`useMessage`） | `Message`（静态导入） | 命令式：`import { Message } from '@arco-design/web-vue'`，无需 provider 包裹 |
| `n-dialog`（`useDialog`） | `Modal.confirm()` / `Modal.warning()` | `useDialog().warning` → `Modal.warning({title, content, onOk})` |
| `n-tag` | `a-tag` | `type` 取值：success/warning/danger/info；`round` 兼容 |
| `n-tabs` / `n-tab-pane` | `a-tabs` / `a-tab-pane` | `v-model:value` → `v-model:active-key` |
| `n-pagination` | `a-pagination` | `@update:page` → `@change`；`item-count` → `total` |
| `n-empty` | `a-empty` | `description` 属性兼容 |
| `n-upload` | `a-upload` | 自定义请求 API 不同（`custom-request` 结构差异大） |
| `n-spin` | `a-spin` | 兼容性好 |
| `n-avatar` | `a-avatar` | `:size` 兼容；`shape="circle"` 默认 |
| `n-result` | `a-result` | `status` 取值兼容 |
| `n-config-provider` | （无，CSS 变量） | 主题改用设计令牌 CSS 变量（`--primary-6` 等），或 `ConfigProvider` 的 component-config |
| `useNotification` | `Notification` | 静态导入 |

## 三、分阶段计划（每阶段可独立提交、可中途停止）

| 阶段 | 内容 | 验收标准 | 预计耗时 |
| --- | --- | --- | --- |
| **P0 基础设施** | `main.ts` Arco 注册与按需优化；`App.vue` Provider 重构（去 Naive provider 壳）；全局主题（设计令牌 + 字体）；`utils/arco.ts` 命令式 API 封装 | 现有页面无回归，试点页正常 | 0.5 天 |
| **P1 公共组件层** | `components/layout`、`components/admin`、`components/person`、`components/tag` 等按域迁移（约 40 个组件） | 每域迁移后 build + 对应页面抽查 | 1 天 |
| **P2 视图层** | `views/` 下 12 个视图 + 404/布局迁移 | 全部路由可访问，功能等价 | 1~1.5 天 |
| **P3 收尾** | `npm test` 全量（vitest 纯函数不受影响）、lint 0 error、build 通过；删除 Naive 依赖与 `app.use(naive)`；样式清理（inline style 统一走设计令牌） | 全量测试通过，bundle 体积对比 | 0.5 天 |

## 四、关键风险与对策

1. **表格/表单 API 差异最大**（n-data-table → a-table）：逐个迁移时先看 Arco 文档，`render` 签名、行选择、排序、固定列均有差异；建议先迁移一个含复杂表格的页面（数据治理）验证。
2. **命令式 API**：`useMessage`/`useDialog` 全部改为静态导入（`Message`/`Modal`），注意 `Modal.confirm` 的异步 `onOk` 支持返回 Promise。
3. **主题系统**：Naive 的 `n-config-provider theme` 属性替换为 Arco 设计令牌 CSS 变量（全局 `:root` 覆盖）；浅色/深色切换改用 `body[arco-theme='dark']`。
4. **`h()` render 兼容**：现有大量 `h('n-xxx')` 字符串组件标签在 render 中的用法需改为导入组件对象（`h(NButton, ...)` 模式，试点页已验证）。
5. **体积**：当前 Arco 全量注册 + Naive 并存，主 chunk 已 1.5MB+；P3 必须删 Naive 依赖并评估 Arco 按需引入（`@arco-design/web-vue/es` + unplugin-vue-components 或手动按需）。
6. **NODE_ENV 陷阱**：本机 `NODE_ENV=production` 会使 npm 跳过 devDependencies——安装依赖务必 `npm ci --include=dev`（本次已踩坑修复）。

## 五、回滚与提交策略

- 回滚：任意阶段失败可 `git checkout pre-arco-pilot` 整体回退；单阶段回退用 `git revert`。
- 提交：每阶段独立 commit（feat: xxx 迁移至 Arco），保持粒度可 bisect；阶段内小修合并提交。
- 迁移期间保留 `/persons-arco` 试点路由，作为新旧观感对比基准，P3 完成后再移除。

## 六、迁移进度

> **✅ 已完成（2026-08-19 全量迁移收官）**：Naive UI 引用 1050 → **0 处**；`naive-ui` 与 `@vicons/ionicons5` 已从 package.json 移除；`app.use(naive)`、`n-config-provider` 壳、`useMessage/useDialog` 全部清除。

### 各域迁移提交记录

| 批次 | 提交 | 内容 |
| --- | --- | --- |
| 试点 | `5693c83` | ArcoPersonPilotView 试点页 + 全局主题 + 迁移方案 |
| 首批 | `d684d61` | PersonTypeTag / SchemaVersionBanner / SearchContextBar / AdminStatCards / AdminDistStats / ImageLightbox |
| 二批 | `58fffb8` | AdminExportPanel / UploadDropZone |
| 三批 | `10cf1e2` | MasonryGrid / UploadQueue |
| 四批 | `cf6fcc9` | ScraperLogViewer |
| 五批 | `1f122a9` | ScraperStatsPanel |
| 六批 | `6040408` | FaceDetectionSection |
| admin 域 | `a7df8dd` | 15 个 admin 组件 |
| inspiration/search 域 | `c3b2a87` | 12 个组件 |
| tag 域 | `7e37483` | 14 个组件 |
| person 域 | `a301e7e` | PersonFormModal / PersonLinkSection / PersonListSection |
| scraper 域 | `7df8aef` | 6 个组件 |
| model 域 | `00836a7` | 10 个组件（含 AnalysisHistoryCard / ModelListPanel / SettingsPanel 等大件） |
| 小视图批 | `e676fe8` | 11 个小视图 + AppLayout（a-menu + Arco 图标库）+ UploadOptionsPanel |
| 视图大件 + P3 | `e01878b` | DetailView / HomeView / PersonDetailView / ModelPhotoUploadView / TaskList / taskLabel / App.vue / main.ts / 20 个 composables / 测试 |

### 收官验证

- `npx vue-tsc --noEmit`：0 error
- `npm run lint`：0 error（43 个既有 no-explicit-any warning 渐进清理）
- `npx vitest run`：104 全过（测试 mock 与断言随语义更新）
- `npm run build`：vite build 成功

### 踩坑记录（后续迁移必读）

1. Arco `Statistic.value` 仅接受 `number | Date`，字符串需自绘文本（见 AdminStatCards）
2. Arco `Button` 无 `error` 类型 → `type="primary" + status="danger"`；尺寸体系 `mini/small/medium/large`（naive `tiny` → `mini`）
3. Arco `Spin.size` 仅接受数字（naive 字符串尺寸无效）
4. `n-input` 的 `@update:value` → `a-input` 的 `@input`；回车 `@keyup.enter` → `@press-enter`
5. `useMessage/useDialog` → 静态 `Message/Modal`（无需 provider 包裹）
6. 本机 `NODE_ENV=production`：装依赖必须 `npm ci --include=dev`
7. Arco `Select.modelValue` 类型不含 `null`：null 哨兵 → `undefined`（v-model 直连）或 `:model-value` + `@change` 转换
8. `a-input` 无 `type="textarea"`：用独立 `a-textarea` 组件
9. `TableColumnData`：`key` → `dataIndex`；`ellipsis` 仅 boolean（tooltip 独立字段）；`render({ record })` 内 `record as T` 转型
10. Arco 图标从 `@arco-design/web-vue/es/icon` 导入（根导出无 Icon 组件）；无 IconDiff 用 IconSync 替代
11. `a-upload` 的 `custom-request`：同步返回 `UploadRequest`（对象），文件在 `fileItem.file`；`@change` 签名 `(fileList, fileItem)`
12. `a-pagination`：`v-model:current` + `:total`（无 page-count）；改页大小用 `@page-size-change`（配 `:auto-adjust="false"` 防双重搜索）
13. Tag/语义色：naive `success/warning/error/info/default` → Arco 预设色 `green/orange/red/arcoblue/gray`（按需映射）
14. 图标类任务按钮：`NIcon + ionicons` → 直接渲染 Arco 图标组件（`h(IconXxx)` 或 `<IconXxx />`）
15. `a-tabs`：`v-model:value` → `v-model:active-key`；`n-tab-pane` 的 `name` → `key`、`tab` → `title`
16. 迁移大文件时注意 `</n-xxx>` 闭合标签残留：开头标签替换后闭合标签易漏，统一 grep 复查
