/** 设置页：查看/修改后端 API 地址（持久化，含连接测试）。
 *
 * 真机使用必须把地址改为电脑的局域网 IP（默认只适配模拟器）；
 * 保存后写入 AsyncStorage，下次启动自动恢复。
 *
 * 审查修复（P0-10）：
 * - 一屏只有一个实心主按钮（「保存」）；「测试连接」降级为描边按钮，结果行内展示，
 *   不再弹 Alert 打断（改前两个同尺寸实心按钮，用户不知道哪个是主操作）
 * - 入口从「白卡套白卡」改成一张分组列表卡（行 + 1px 分隔线）
 * - 底部显示版本号（从 app.json 读，避免手工写死后对不上 APK）
 */

import { useEffect, useState } from 'react'
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, View } from 'react-native'
import { useRouter } from 'expo-router'
import ScreenHeader from '../../components/ui/ScreenHeader'
import ListRow from '../../components/ui/ListRow'
import Button from '../../components/ui/Button'
import AppText from '../../components/ui/AppText'
import Icon from '../../components/Icon'
import { useStyles, useTheme } from '../../hooks/useTheme'
import {
  getApiBaseUrl,
  loadApiBaseUrl,
  saveApiBaseUrl,
  testApiConnection,
} from '../../services/api'
import { useInspirationStore } from '../../hooks/useInspirations'
import appConfig from '../../app.json'
import type { Theme } from '../../constants/theme'

/** 连接测试结果（行内展示，不弹窗） */
interface TestResult {
  ok: boolean
  message: string
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
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)

  useEffect(() => {
    void loadApiBaseUrl().then((url) => setInput(url))
  }, [])

  /** 测试连接：调后端 /api/health，结果行内展示 */
  const handleTest = async () => {
    if (!input.trim()) {
      setResult({ ok: false, message: '请先填写后端地址' })
      return
    }
    setTesting(true)
    setResult(null)
    try {
      const next = await testApiConnection(input)
      setResult(next)
    } finally {
      setTesting(false)
    }
  }

  /** 保存地址：持久化 + 更新全局地址 + 重新拉取素材 */
  const handleSave = async () => {
    if (!input.trim()) {
      setResult({ ok: false, message: '请先填写后端地址' })
      return
    }
    setSaving(true)
    try {
      await saveApiBaseUrl(input)
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
    about: { textAlign: 'center', paddingTop: t.space.xl },
  })
