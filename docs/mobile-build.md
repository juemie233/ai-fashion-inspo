# 移动端构建（Android APK）

> 本文从 `CLAUDE.md` 迁出：它是**环境特定的操作手册**（本机绝对路径、代理端口、
> Defender 排除项、插件版本），不属于跨机器通用的项目标准。
> 换机器时按本文替换路径与端口即可，标准本身不用动。

手机没有 Google Play → 装不了 Expo Go → **只能自建 APK**。下面这些不是建议，是实测撞出来的固定动作（2026-10-23）。

**工具链（一次性，本机已装）**

| 组件 | 位置 |
|---|---|
| JDK 17 | `C:\Users\Administrator\jdk17\jdk-17.0.20.1+1`（系统默认 java 是 25，Gradle 8.10 不认，**必须显式 `JAVA_HOME`**） |
| Android SDK | `C:\Users\Administrator\AndroidSdk`（platform-tools / platforms;android-35 / build-tools 35 / NDK 26.1 / CMake 3.22） |

**必须在短真实路径构建**

项目在 `...\Desktop\Claude Code\...`（带空格），CMake 会把源路径压成 `Claude_Code`，react-native-reanimated 的 C++ 对象路径超 Windows MAX_PATH → ninja `mkdir` 失败。`mklink /J` 和 `subst` 都会被 Gradle/CMake 解回真实路径（都试过，无效）——只能**真实复制**到短路径构建：

```bash
robocopy "<repo>\mobile" "C:\mb" /MIR
cd C:\mb\android
set JAVA_HOME=C:\Users\Administrator\jdk17\jdk-17.0.20.1+1
set ANDROID_HOME=C:\Users\Administrator\AndroidSdk
.\gradlew assembleRelease
# 产物：C:\mb\android\app\build\outputs\apk\release\app-release.apk
```

**版本号**：`android/` 是 prebuild 生成物且已 gitignore，`versionCode`/`versionName` 直接改 `C:\mb\android\app\build.gradle`，且必须用**正则**改——`/MIR` 会用仓库里的旧 `build.gradle` 覆盖副本，按字符串替换会静默失配（踩过：打的包版本号没变）。

**prebuild 重跑后要补的两行**（`android/` 会被重置）

- `android.kotlinVersion=1.9.24` —— RN 0.76 带的是 Kotlin 1.9.24，而 `android/build.gradle` 的 ext 默认 1.9.25，会让 expo-modules-core 报 Compose 1.5.15 与编译器不匹配
- `android.usesCleartextTraffic: true`（写在 `app.json` 的 `expo-build-properties` 里，不需手改 gradle）

**提速配方（实测 6m37s → 31s；无改动重跑 9s）**

| 做法 | 收益 | 说明 |
|---|---|---|
| **不要加 `--no-daemon`** | 最大 | 冷启 JVM + 重新配置要 2~3 分钟，daemon 复用后配置阶段几秒 |
| **纯 JS 改动不要清** `android/.cxx` / `build/` | 最大 | 清一次就全量重编 reanimated、expo-modules 的 C++（几分钟）；只有动原生依赖才需要清 |
| `reactNativeArchitectures=arm64-v8a` | 中 | 手机是 arm64，编 4 个 ABI 纯浪费（仓库侧 `gradle.properties` 已配） |
| `org.gradle.caching=true` + `jvmargs -Xmx4096m` | 中 | 任务产物复用；2G heap 偏小（已配） |
| **Defender 排除** `C:\mb`、`~/.gradle`、`AndroidSdk`、仓库目录 | 大 | 实时扫描极拖慢（62s → 31s）；`Add-MpPreference -ExclusionPath` 需管理员（已配） |
| 不要开 `--configuration-cache` | —— | Expo/RN 插件有 45 处不兼容，直接 BUILD FAILED（试过） |

**两个环境陷阱**

- Gradle 依赖走本机代理（clash 7890）：`android/gradle.properties` 里 `systemProp.http(s).proxyHost/Port`；代理没开就删掉这几行（依赖已缓存时不影响增量构建）
- **npm 不要设代理**：它走 npmmirror，设了必 `ECONNREFUSED`

**日常改 UI 的更快路子（未落地）**：装一次 debug APK + `npx expo start --host lan`，手机同网即可 Fast Refresh，改 JS 秒级生效、完全不用打包。

**分享 Intent（`expo-share-intent`）**：只做 Android，版本 `3.1.0`（对应 SDK 52；4.x 起要 SDK 53+）。`app.json` 里配 `androidIntentFilters: ["image/*","text/*"]` + `disableIOS: true`，**prebuild 会把它写进 AndroidManifest 的 `ACTION_SEND`**（改配置后必须重跑 prebuild）。处理逻辑在 `components/ShareIntentBridge.tsx`，处理完必须 `resetShareIntent()`，否则下次冷启动会重复入库。

**两个构建期坑**：① `npm i --legacy-peer-deps` 会**删掉一批包**（实测两次：一次删掉 `expo-linking` 导致打包失败，一次删 63 个），装完依赖要立刻检查 `expo-linking` / `expo-font` / `expo-asset` / `expo-router` 是否还在；② `:app:mergeDexRelease` 报「另一个程序正在使用此文件」时，`./gradlew --stop` + 删 `app/build/intermediates/dex/release/mergeDexRelease` 再构建即可。

**视频播放（`expo-video`）**：SDK 52 对应的版本是 `~2.0.6`（`expo-av` 已弃用）。它是**原生模块**——装完必须重跑 `prebuild` + 完整重建，否则运行时报「找不到模块」。`app.json` 里配 `["expo-video", { "supportsPictureInPicture": true, "supportsBackgroundPlayback": false }]`，不配插件时能播但会刷 `Current activity does not support picture-in-picture` 告警。播放器封装在 `components/VideoPlayer.tsx`（封面帧 + 点击播放 + 原生控件 + 全屏），详情页按 `media_type === 'video'` 分流；素材库卡片给视频加「视频」角标。

**冷启动请求竞态（2026-10-28 真机定位）**：页面在 mount 时就发请求，而 `loadApiBaseUrl()`（读 AsyncStorage）还没跑完 → baseURL 仍是默认的 `10.0.2.2` → 请求必失败、界面显示「加载失败」，用户重进一次又好了，表现为「时好时坏」。修法是在 `apiClient` 的**请求拦截器里 `await ensureBaseUrl()`**（缓存 Promise，只付一次成本）；`saveApiBaseUrl` 后要同步刷新该 Promise。以后新增任何「启动即请求」的页面都不用再关心这个顺序。

**增量同步不要用 `/MIR`**：`/MIR` 会删掉 `C:\mb\android\build`、`.cxx`，等于每次全量重编原生（几分钟）。日常改 JS 用 `/E`（只覆盖不删除），保留构建缓存后一次 **30~40 秒**；只有依赖大改/换 prebuild 产物时才 `/MIR`。

**图标必须用 `mobile/components/Icon.tsx`，不要写 `<Ionicons>`**（2026-10-24 真机踩坑）

- 现象：真机上**所有图标**（tab、按钮、爱心、返回箭头）全部空白，且没有任何报错日志
- 根因两层：① `@expo/vector-icons` 在 Android 用**小写文件名**做 fontFamily（`'ionicons'`），字体必须内嵌为 `assets/fonts/ionicons.ttf` 才能被原生字体表找到；② 更致命的是组件挂载前先判 `Font.isLoaded('ionicons')`，而 expo-font 只把**自己 loadAsync 过的**字体算已加载，内嵌字体不算 → 判假 → 组件直接渲染空 `<Text />`
- 结论：字体已由 `app.json` 的 `expo-font` plugin 内嵌（`./assets/fonts/Ionicons.ttf`，探针验证 `fontFamily: 'Ionicons'` 可渲染），`Icon.tsx` 直接用该 family 画码点，绕过那层判断；**不要再改回 `<Ionicons>`**，也不要在 `_layout` 里用 `useFonts` 做字体门（字体加载失败会让整页停在白屏底板）
- 教训：这类问题**只有真机能复现**（Jest / react-native-web 都不校验），排查时用 `adb logcat` + 探针（同一屏并排渲染几种写法对比）最快
