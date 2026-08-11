/** 采集页：拍照 / 从相册选择上传。 */

import { useState } from 'react'
import {
  View,
  Text,
  TouchableOpacity,
  Image,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import { Ionicons } from '@expo/vector-icons'
import { useInspirationStore } from '../../hooks/useInspirations'

export default function CaptureScreen() {
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploaded, setUploaded] = useState(false)
  const { uploadImage } = useInspirationStore()

  /** 从相册选择 */
  const pickFromGallery = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在设置中允许访问相册')
      return
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.9,
      allowsMultipleSelection: false,
    })

    if (!result.canceled && result.assets?.[0]) {
      setSelectedImage(result.assets[0].uri)
      setUploaded(false)
    }
  }

  /** 拍照 */
  const takePhoto = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync()
    if (!permission.granted) {
      Alert.alert('权限不足', '请在设置中允许使用相机')
      return
    }

    const result = await ImagePicker.launchCameraAsync({
      quality: 0.9,
    })

    if (!result.canceled && result.assets?.[0]) {
      setSelectedImage(result.assets[0].uri)
      setUploaded(false)
    }
  }

  /** 上传到后端 */
  const handleUpload = async () => {
    if (!selectedImage) return
    setUploading(true)

    try {
      await uploadImage(selectedImage)
      setUploaded(true)
      Alert.alert('上传成功', '素材已入库，AI 将自动分析标签')
    } catch (e: any) {
      Alert.alert('上传失败', e.message || '请确认后端已启动且手机与电脑在同一局域网')
    } finally {
      setUploading(false)
    }
  }

  return (
    <View style={styles.container}>
      {/* 预览区 */}
      <View style={styles.previewArea}>
        {selectedImage ? (
          <Image source={{ uri: selectedImage }} style={styles.previewImage} />
        ) : (
          <View style={styles.previewPlaceholder}>
            <Ionicons name="image-outline" size={80} color="#d1d5db" />
            <Text style={styles.placeholderText}>选择或拍摄一张穿搭照片</Text>
          </View>
        )}

        {uploaded && (
          <View style={styles.uploadedBadge}>
            <Ionicons name="checkmark-circle" size={20} color="#16a34a" />
            <Text style={styles.uploadedText}>已上传</Text>
          </View>
        )}

        {uploading && (
          <View style={styles.uploadingOverlay}>
            <ActivityIndicator size="large" color="#fff" />
            <Text style={styles.uploadingText}>上传中...</Text>
          </View>
        )}
      </View>

      {/* 操作按钮 */}
      <View style={styles.actions}>
        <TouchableOpacity style={styles.actionBtn} onPress={takePhoto}>
          <View style={[styles.actionIcon, { backgroundColor: '#6366f1' }]}>
            <Ionicons name="camera" size={28} color="#fff" />
          </View>
          <Text style={styles.actionLabel}>拍照</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.actionBtn} onPress={pickFromGallery}>
          <View style={[styles.actionIcon, { backgroundColor: '#8b5cf6' }]}>
            <Ionicons name="images" size={28} color="#fff" />
          </View>
          <Text style={styles.actionLabel}>相册</Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[
            styles.uploadBtn,
            !selectedImage && styles.uploadBtnDisabled,
          ]}
          onPress={handleUpload}
          disabled={!selectedImage || uploading}
        >
          <Text style={styles.uploadBtnText}>
            {uploading ? '上传中...' : '上传入库'}
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  previewArea: {
    flex: 1,
    margin: 16,
    borderRadius: 12,
    backgroundColor: '#fff',
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#e5e7eb',
    borderStyle: 'dashed',
  },
  previewImage: {
    width: '100%',
    height: '100%',
    resizeMode: 'contain',
  },
  previewPlaceholder: {
    alignItems: 'center',
    padding: 40,
  },
  placeholderText: { fontSize: 15, color: '#9ca3af', marginTop: 12 },
  uploadedBadge: {
    position: 'absolute',
    top: 12,
    right: 12,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#f0fdf4',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    gap: 4,
  },
  uploadedText: { fontSize: 13, color: '#16a34a', fontWeight: '500' },
  uploadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  uploadingText: { color: '#fff', fontSize: 16, marginTop: 8 },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    gap: 12,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  actionBtn: { alignItems: 'center', gap: 4 },
  actionIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionLabel: { fontSize: 12, color: '#6b7280' },
  uploadBtn: {
    flex: 1,
    backgroundColor: '#6366f1',
    paddingVertical: 16,
    borderRadius: 10,
    alignItems: 'center',
    marginLeft: 'auto',
  },
  uploadBtnDisabled: { backgroundColor: '#c7d2fe' },
  uploadBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
})
