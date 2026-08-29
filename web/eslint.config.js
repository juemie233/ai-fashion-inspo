/**
 * ESLint 扁平配置：Vue 3 + TypeScript + Prettier 协同。
 * 规则保持克制：类型/未用变量报 warn 不阻断，格式交给 Prettier 单独校验，
 * 避免 lint 与格式化职责混淆（Prettier 冲突规则由 eslint-config-prettier 关闭）。
 */
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import prettier from 'eslint-config-prettier'

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
  {
    rules: {
      // 项目现有代码风格：组件名可单词（如 App.vue），不强制多词
      'vue/multi-word-component-names': 'off',
      // TS 已做未定义变量检查，no-undef 对浏览器全局（document/URL/File 等）误报
      'no-undef': 'off',
      // 属性顺序属风格偏好，交给团队约定而非 lint 强制
      'vue/attributes-order': 'off',
      // any 在 catch(e)/动态载荷中仍大量存在：告警但不阻断，渐进清理
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      // 项目大量使用「静默降级」空 catch（有注释说明），允许空 catch 块
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-console': 'off',
      'no-debugger': 'warn',
    },
  },
  {
    // 标签管理大列表：v-for 行上使用 v-memo 做 per-item 缓存（勾选仅重渲受影响行）。
    // Vue 编译器原生支持该写法，vue/valid-v-memo 对「v-for 内 v-memo」的拦截为误报。
    files: ['src/components/tag/TagGroupList.vue'],
    rules: {
      'vue/valid-v-memo': 'off',
    },
  },
  prettier,
)
