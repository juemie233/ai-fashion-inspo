/** 设置页：查看/修改后端 API 地址（持久化，含连接测试）。

 * 真机使用必须把地址改为电脑的局域网 IP（默认只适配模拟器）；
 * 保存后写入 AsyncStorage，下次启动自动恢复。
 */

import { useEffect, useState } from 'react'
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import {
  getApiBaseUrl,
  loadApiBaseUrl,
  saveApiBaseUrl,
  testApiConnection,
} from '../services/api'
import { useInspirationStore } from '../hooks/useInspirations'

export default function SettingsScreen() {
  const [input, setInput] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    loadApiBaseUrl().then((url) => setInput(url))
  }, [])

  /** 测试连接：调后端 /api/health */
  const handleTest = async () => {
    if (!input.trim()) {
      Alert.alert('提示', '请先填写后端地址')
      return
    }
    setTesting(true)
    try {
      const result = await testApiConnection(input)
      Alert.alert(result.ok ? '连接成功' : '连接失败', result.message)
    } finally {
      setTesting(false)
    }
  }

  /** 保存地址：持久化 + 更新全局地址 + 重新拉取素材 */
  const handleSave = async () => {
    if (!input.trim()) {
      Alert.alert('提示', '请填写后端地址')
      return
    }
    setSaving(true)
    try {
      await saveApiBaseUrl(input)
      // 同步 store 里的地址（图片/上传等拼接文件 URL 用），并刷新列表
      useInspirationStore.setState({ apiBaseUrl: getApiBaseUrl() })
      await useInspirationStore.getState().fetchInspirations()
      Alert.alert('已保存', '后端地址已更新，素材列表已刷新')
    } catch {
      Alert.alert('保存失败', '请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.card}>
        <View style={styles.field}>
          <Text style={styles.label}>后端 API 地址</Text>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="http://192.168.x.x:18888"
            placeholderTextColor="#9ca3af"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
          />
        </View>

        <Text style={styles.hint}>
          手机与电脑需在同一 Wi-Fi；填电脑的局域网 IP（ipconfig 查看 WLAN 的 IPv4
          地址），端口默认 18888。模拟器使用默认地址即可。
        </Text>

        <TouchableOpacity
          style={[styles.btn, styles.testBtn]}
          onPress={handleTest}
          disabled={testing}
        >
          {testing ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <>
              <Ionicons name="pulse-outline" size={18} color="#fff" />
              <Text style={styles.btnText}>测试连接</Text>
            </>
          )}
        </TouchableOpacity>

        <TouchableOpacity
          style={[styles.btn, styles.saveBtn, saving && styles.btnDisabled]}
          onPress={handleSave}
          disabled={saving}
        >
          {saving ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <>
              <Ionicons name="checkmark-circle-outline" size={18} color="#fff" />
              <Text style={styles.btnText}>保存并刷新</Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb', padding: 16 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.06,
    shadowRadius: 4,
    elevation: 2,
  },
  field: { marginBottom: 12 },
  label: { fontSize: 14, color: '#374151', fontWeight: '500', marginBottom: 8 },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
    fontSize: 15,
    color: '#1f2937',
    backgroundColor: '#fff',
  },
  hint: {
    fontSize: 12,
    color: '#9ca3af',
    lineHeight: 18,
    marginBottom: 16,
  },
  btn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderRadius: 10,
    paddingVertical: 14,
    marginBottom: 10,
  },
  testBtn: { backgroundColor: '#8b5cf6' },
  saveBtn: { backgroundColor: '#6366f1' },
  btnDisabled: { opacity: 0.6 },
  btnText: { color: '#fff', fontSize: 15, fontWeight: '600' },
})
