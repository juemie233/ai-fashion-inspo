/** 视频播放器：封面帧 + 点击播放 + 原生控制条（含全屏）。
 *
 * 为什么用 expo-video：SDK 52 起 expo-av 已弃用，expo-video 是官方替代；后端视频是
 * 普通 mp4（`/api/files/...mp4`），原生播放器配 nativeControls 就够了，不需要自定义
 * 进度条——自定义那套在手机上反而难用（拖动精度差）。
 *
 * 交互：默认显示封面帧 + 大播放按钮（避免自动播放耗流量），点一下开始播放后显示原生控件。
 */

import { useState } from 'react'
import { Image, StyleSheet, TouchableOpacity, View, type StyleProp, type ViewStyle } from 'react-native'
import { useVideoPlayer, VideoView } from 'expo-video'
import Icon from './Icon'

interface Props {
  /** 视频完整地址 */
  uri: string
  /** 封面帧地址（后端 thumbnail_path 生成的那张 jpg），可为空 */
  posterUri?: string
  /** 容器尺寸；默认 16:9 铺满宽度 */
  style?: StyleProp<ViewStyle>
}

export default function VideoPlayer({ uri, posterUri, style }: Props) {
  /** 是否已开始播放（用于隐藏封面与播放按钮） */
  const [started, setStarted] = useState(false)

  const player = useVideoPlayer(uri || null, (p) => {
    // 短视频素材：循环播更方便反复看细节
    p.loop = true
  })

  const handlePlay = () => {
    setStarted(true)
    player.play()
  }

  return (
    <View style={[styles.container, style]}>
      <VideoView
        testID="video-player"
        style={styles.video}
        player={player}
        nativeControls
        allowsFullscreen
        contentFit="contain"
      />

      {/* 封面 + 播放按钮：未开始播放时盖在上面 */}
      {!started && (
        <TouchableOpacity
          testID="video-poster"
          style={styles.posterLayer}
          activeOpacity={0.9}
          onPress={handlePlay}
        >
          {posterUri ? (
            <Image source={{ uri: posterUri }} style={styles.poster} resizeMode="cover" />
          ) : (
            <View style={[styles.poster, styles.posterEmpty]} />
          )}
          <View style={styles.playBtn}>
            <Icon name="play" size={28} color="#fff" />
          </View>
        </TouchableOpacity>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#000' },
  video: { width: '100%', height: '100%' },
  posterLayer: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
  },
  poster: { ...StyleSheet.absoluteFillObject, width: '100%', height: '100%' },
  posterEmpty: { backgroundColor: '#111827' },
  playBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: 'rgba(0,0,0,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
  },
})
