/** 设置页：后端地址 + API Key + 连接诊断 + 缓存管理 + 浏览入口。
 *
 * 真机使用必须把地址改为电脑的局域网 IP（默认只适配模拟器）；
 * 保存后写入 AsyncStorage，下次启动自动恢复。
 *
 * 审查修复（P0-10）：
 * - 一屏只有一个实心主按钮（「保存」）；「测试连接」降级为描边按钮，结果行内展示，
 *   不再弹 Alert 打断（改前两个同尺寸实心按钮，用户不知道哪个是主操作）
 * - 入口从「白卡套白卡」改成一张分组列表卡（行 + 1px 分隔线）
 *
 * P1 补齐（弱网/设置页信息与缓存 + API Key）：
 * - **API Key**：后端设置 `API_KEY` 后除回环地址外的请求都要带 `X-API-Key`；
 *   输入框保存到本机 AsyncStorage（不打印、不回显整串），连接测试也带 Key；
 * - **地址免手敲**：复制当前地址 / 从剪贴板粘贴（手输局域网 IP 最容易打错）；
 * - **诊断信息**：版本、当前地址、Key 状态、离线缓存条数与时间；
 * - **缓存清理**：清离线缓存 + 图片内存/磁盘缓存（清完图片会重新下载）。
 */

import { useEffect, useState } from 'react'
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native'
import { Image } from 'expo-image'
import { useRouter } from 'expo-router'
import ScreenHeader from '../../components/ui/ScreenHeader'
import ListRow from '../../components/ui/ListRow'
import Button from '../../components/ui/Button'
import AppText from '../../components/ui/AppText'
import Icon from '../../components/Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import {
  getApiBaseUrl,
  getApiKey,
  loadApiBaseUrl,
  loadApiKey,
  saveApiBaseUrl,
  saveApiKey,
  testApiConnection,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import {
  clearOfflineCache,
  formatCacheTime,
  loadGalleryCache,
  loadPopularTags,
} from '../../utils/offlineCache'
import { copyText, readClipboard } from '../../utils/clipboard'
import appConfig from '../../app.json'
import type { Theme } from '../../constants/theme'

/** 连接测试结果（行内展示，不弹窗） */
interface TestResult {
  ok: boolean
  message: string
}

/** 缓存概况（诊断区展示） */
interface CacheInfo {
  galleryCount: number
  gallerySavedAt: number | null
  tagCount: number
}

/** 浏览器入口（设置页的「浏览」分组） */
const LINKS: { href: string; icon: string; label: string; hint: string }[] = [
  { href: '/tasks', icon: 'pulse-outline', label: '后台任务', hint: 'AI 分析 / 采集 / 出图进度' },
  { href: '/persons', icon: 'people-outline', label: '人物', hint: '穿搭博主与模特作品墙' },
  { href: '/tags', icon: 'pricetags-outline', label: '标签管理', hint: '浏览、搜索与改名' },
  { href: '/generate', icon: 'sparkles-outline', label: '出图记录', hint: '查看本机出图批次与候选图' },
]

export default function SettingsScreen() {
  const router = useRouter()
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  const [input, setInput] = useState('')
  const [keyInput, setKeyInput] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [cacheInfo, setCacheInfo] = useState<CacheInfo>({
    galleryCount: 0,
    gallerySavedAt: null,
    tagCount: 0,
  })
  /** 缓存清理中（禁重复点击） */
  const [clearing, setClearing] = useState(false)

  /** 刷新缓存概况（进入页面、清理后都调） */
  const refreshCacheInfo = async () => {
    const [gallery, tags] = await Promise.all([loadGalleryCache(), loadPopularTags()])
    setCacheInfo({
      galleryCount: gallery?.items.length ?? 0,
      gallerySavedAt: gallery?.savedAt ?? null,
      tagCount: tags.length,
    })
  }

  useEffect(() => {
    void loadApiBaseUrl().then((url) => setInput(url))
    // Key 回显：只填掩码，不把整串 Key 摊在屏幕上
    void loadApiKey().then((key) => setKeyInput(key ? '••••••••' : ''))
    void refreshCacheInfo()
  }, [])

  /** 测试连接：调后端 /api/health（带 Key），结果行内展示 */
  const handleTest = async () => {
    if (!input.trim()) {
      setResult({ ok: false, message: '请先填写后端地址' })
      return
    }
    setTesting(true)
    setResult(null)
    try {
      // 掩码表示「沿用已保存的 Key」，用户没动过就不该拿掩码去测
      const key = keyInput === '••••••••' ? getApiKey() : keyInput.trim()
      const next = await testApiConnection(input, key)
      setResult(next)
    } finally {
      setTesting(false)
    }
  }

  /** 复制当前地址（手把手抄 IP 最容易出错） */
  const handleCopy = async () => {
    const next = await copyText(input || getApiBaseUrl())
    setResult({ ok: next.ok, message: next.message })
  }

  /** 从剪贴板粘贴地址（Web 端/电脑上复制过来的局域网 IP） */
  const handlePaste = async () => {
    const text = await readClipboard()
    if (!text) {
      setResult({ ok: false, message: '剪贴板里没有可用文本' })
      return
    }
    // 只取第一行，避免把整段说明文粘进来
    setInput(text.split('\n')[0].trim())
    setResult({ ok: true, message: '已从剪贴板填入地址' })
  }

  /** 保存地址与 Key：持久化 + 更新全局地址 + 重新拉取素材 */
  const handleSave = async () => {
    if (!input.trim()) {
      setResult({ ok: false, message: '请先填写后端地址' })
      return
    }
    setSaving(true)
    try {
      await saveApiBaseUrl(input)
      // 掩码 = 保持原 Key；空 = 清除；其它 = 新 Key
      if (keyInput !== '••••••••') await saveApiKey(keyInput)
      setKeyInput(getApiKey() ? '••••••••' : '')
      // 同步 store 里的地址（图片/上传等拼接文件 URL 用），并刷新列表
      useInspirationStore.setState({ apiBaseUrl: getApiBaseUrl() })
      await useInspirationStore.getState().fetchInspirations()
      setResult({ ok: true, message: '已保存，素材列表已刷新' })
    } catch {
      setResult({ ok: false, message: '保存失败，请稍后重试' })
    } finally {
      setSaving(false)
    }
  }

  /** 清理缓存：离线缓存 + 图片内存/磁盘缓存 */
  const handleClearCache = async () => {
    setClearing(true)
    try {
      await clearOfflineCache()
      // 图片缓存清理失败不影响离线缓存已清的结果
      await Promise.all([
        Image.clearMemoryCache().catch(() => false),
        Image.clearDiskCache().catch(() => false),
      ])
      await refreshCacheInfo()
      setResult({ ok: true, message: '缓存已清理（图片会在下次浏览时重新下载）' })
    } catch {
      setResult({ ok: false, message: '清理缓存失败，请稍后重试' })
    } finally {
      setClearing(false)
    }
  }

  const keySaved = getApiKey().length > 0

  return (
    <View style={styles.container}>
      <ScreenHeader title="设置" />
      <KeyboardAvoidingView
        style={styles.fill}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.content}>
          {/* 服务连接 */}
          <AppText variant="caption" color="textTertiary" style={styles.groupLabel}>
            服务连接
          </AppText>
          <View style={styles.card}>
            <AppText variant="bodySm" color="textSecondary">
              后端 API 地址
            </AppText>
            <TextInput
              style={styles.input}
              value={input}
              onChangeText={setInput}
              placeholder="http://192.168.x.x:18888"
              placeholderTextColor={theme.color.textTertiary}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              testID="api-url-input"
            />
            <View style={styles.inlineRow}>
              <Pressable
                onPress={() => void handleCopy()}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel="复制后端地址"
                testID="copy-address"
              >
                <AppText variant="caption" color="brandText">
                  复制地址
                </AppText>
              </Pressable>
              <Pressable
                onPress={() => void handlePaste()}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel="从剪贴板粘贴地址"
                testID="paste-address"
              >
                <AppText variant="caption" color="brandText">
                  从剪贴板粘贴
                </AppText>
              </Pressable>
            </View>

            <AppText variant="bodySm" color="textSecondary">
              API Key（可选）
            </AppText>
            <TextInput
              style={styles.input}
              value={keyInput}
              onChangeText={setKeyInput}
              placeholder="后端未开启鉴权时留空"
              placeholderTextColor={theme.color.textTertiary}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              testID="api-key-input"
            />
            <AppText variant="caption" color="textTertiary">
              {keySaved
                ? '已设置：保存在本机，请求自动带 X-API-Key'
                : '后端 .env 里设置了 API_KEY 时才需要填；只存本机，不上传'}
            </AppText>

            <AppText variant="caption" color="textTertiary">
              手机与电脑需在同一 Wi-Fi；填电脑的局域网 IP（ipconfig 查看 WLAN 的 IPv4
              地址），端口默认 18888。模拟器使用默认地址即可。
            </AppText>

            {/* 测试连接：描边按钮 + 行内结果 */}
            <View style={styles.testRow}>
              <Button
                title="测试连接"
                icon="pulse-outline"
                variant="secondary"
                onPress={() => void handleTest()}
                loading={testing}
                testID="test-connection"
              />
              {result ? (
                <View style={styles.testResult}>
                  <Icon
                    name={result.ok ? 'checkmark-circle' : 'alert-circle'}
                    size={theme.icon.sm}
                    color={result.ok ? theme.color.successText : theme.color.dangerText}
                  />
                  <AppText
                    variant="caption"
                    color={result.ok ? 'successText' : 'dangerText'}
                    numberOfLines={2}
                    style={styles.flex}
                  >
                    {result.message}
                  </AppText>
                </View>
              ) : null}
            </View>

            {/* 唯一实心主按钮 */}
            <Button
              title="保存"
              icon="checkmark-circle-outline"
              onPress={() => void handleSave()}
              loading={saving}
              fullWidth
              testID="save-connection"
            />
          </View>

          {/* 浏览：一张分组列表卡（不再白卡套白卡） */}
          <AppText variant="caption" color="textTertiary" style={styles.groupLabel}>
            浏览
          </AppText>
          <View style={styles.listCard}>
            {LINKS.map((link, index) => (
              <ListRow
                key={link.href}
                icon={link.icon}
                title={link.label}
                subtitle={link.hint}
                divider={index < LINKS.length - 1}
                onPress={() => router.push(link.href as never)}
              />
            ))}
          </View>

          {/* 诊断与缓存 */}
          <AppText variant="caption" color="textTertiary" style={styles.groupLabel}>
            诊断与缓存
          </AppText>
          <View style={styles.listCard}>
            <ListRow
              icon="information-circle-outline"
              title="版本"
              subtitle={`v${appConfig.expo.version}（build ${appConfig.expo.android?.versionCode ?? '—'}）`}
              showChevron={false}
            />
            <ListRow
              icon="server-outline"
              title="当前地址"
              subtitle={getApiBaseUrl()}
              showChevron={false}
            />
            <ListRow
              icon="key-outline"
              title="API Key"
              subtitle={keySaved ? '已设置' : '未设置（后端未开鉴权时正常）'}
              showChevron={false}
            />
            <ListRow
              icon="download-outline"
              title="离线缓存"
              subtitle={
                cacheInfo.galleryCount > 0
                  ? `素材库 ${cacheInfo.galleryCount} 条 · ${formatCacheTime(cacheInfo.gallerySavedAt ?? 0)}`
                  : '暂无（联网浏览一次素材库后生成）'
              }
              showChevron={false}
              divider={false}
            />
          </View>
          <View style={styles.cacheRow}>
            <Button
              title="清理缓存"
              icon="trash-outline"
              variant="secondary"
              onPress={() => void handleClearCache()}
              loading={clearing}
              testID="clear-cache"
            />
            <AppText variant="caption" color="textTertiary" style={styles.flex}>
              清掉离线缓存与图片缓存，不会删除素材
            </AppText>
          </View>

          <AppText variant="caption" color="textDisabled" style={styles.about}>
            穿搭素材库 v{appConfig.expo.version}
          </AppText>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.color.bgPage },
    fill: { flex: 1 },
    content: { padding: t.space.lg, gap: t.space.sm, paddingBottom: t.space.xxxl },
    groupLabel: { paddingTop: t.space.sm, paddingLeft: t.space.xs },
    card: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
      padding: t.space.lg,
      gap: t.space.md,
    },
    input: {
      minHeight: t.touch,
      borderWidth: 1,
      borderColor: t.color.border,
      borderRadius: t.radius.control,
      paddingHorizontal: t.space.md,
      fontSize: t.font.body.fontSize,
      color: t.color.textPrimary,
      backgroundColor: t.color.bgSurface,
    },
    inlineRow: { flexDirection: 'row', gap: t.space.lg, marginTop: -t.space.sm },
    testRow: { flexDirection: 'row', alignItems: 'center', gap: t.space.md },
    testResult: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: t.space.xs },
    flex: { flex: 1 },
    listCard: {
      backgroundColor: t.color.bgSurface,
      borderRadius: t.radius.card,
      overflow: 'hidden',
      borderWidth: StyleSheet.hairlineWidth,
      borderColor: t.color.divider,
    },
    cacheRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: t.space.md,
      paddingTop: t.space.xs,
      paddingHorizontal: t.space.xs,
    },
    about: { textAlign: 'center', paddingTop: t.space.xl },
  })
