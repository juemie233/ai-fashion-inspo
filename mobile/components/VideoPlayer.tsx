/** 视频播放器：封面帧 + 点击播放 + 原生控制条（含全屏）。
 *
 * 为什么用 expo-video：SDK 52 起 expo-av 已弃用，expo-video 是官方替代；后端视频是
 * 普通 mp4（`/api/files/...mp4`），原生播放器配 nativeControls 就够了，不需要自定义
 * 进度条——自定义那套在手机上反而难用（拖动精度差）。
 *
 * 交互：默认显示封面帧 + 大播放按钮（避免自动播放耗流量），点一下开始播放后显示原生控件。
 *
 * 审查修复（P0-9）：改前容器固定 16:9，抖音/小红书素材绝大多数是 9:16，竖屏视频只剩中间
 * 一条、上下全黑；封面用 `cover` 裁切而播放用 `contain`，点播放画面会「跳一下」。
 * 现在：容器按**封面帧的真实比例**（拿不到时 3:4），封面与视频统一 `contain`，
 * 背后铺一层放大模糊的同款封面兜底，比例不一致时也不会露出两块纯黑。
 */

import { useState } from 'react'
import { StyleSheet, TouchableOpacity, View, type StyleProp, type ViewStyle } from 'react-native'
import { Image } from 'expo-image'
import { useVideoPlayer, VideoView } from 'expo-video'
import Icon from './Icon'
import AppText from './ui/AppText'
import { useStyles, useTheme } from '../hooks/useTheme'
import { useAspectRatio } from '../hooks/useAspectRatio'
import type { Theme } from '../constants/theme'

/** 拿不到封面比例时的兜底（3:4，与素材卡一致；竖屏视频最接近的默认值） */
const DEFAULT_RATIO = 0.75
/** 背景模糊半径 */
const BACKDROP_BLUR = 24

interface Props {
  /** 视频完整地址 */
  uri: string
  /** 封面帧地址（后端 thumbnail_path 生成的那张 jpg），可为空 */
  posterUri?: string
  /** 额外容器样式（一般不用传，比例由封面决定） */
  style?: StyleProp<ViewStyle>
}

export default function VideoPlayer({ uri, posterUri, style }: Props) {
  const theme = useTheme()
  const styles = useStyles(makeStyles)
  /** 是否已开始播放（用于隐藏封面与播放按钮） */
  const [started, setStarted] = useState(false)
  /** 容器比例 = 封面帧比例（P0-9）：首帧用 3:4 兜底，封面加载完按真实比例修正 */
  const { ratio, onLoad } = useAspectRatio(posterUri, DEFAULT_RATIO)

  const player = useVideoPlayer(uri || null, (p) => {
    // 短视频素材：循环播更方便反复看细节
    p.loop = true
  })

  const handlePlay = () => {
    setStarted(true)
    player.play()
  }

  return (
    <View style={[styles.container, { aspectRatio: ratio }, style]}>
      {/* 背景：封面放大模糊铺底，避免比例不一致时上下两块纯黑 */}
      {posterUri ? (
        <Image
          source={{ uri: posterUri }}
          style={StyleSheet.absoluteFill}
          contentFit="cover"
          blurRadius={BACKDROP_BLUR}
          transition={0}
          cachePolicy="memory-disk"
        />
      ) : null}

      <VideoView
        testID="video-player"
        style={styles.video}
        player={player}
        nativeControls
        allowsFullscreen
        contentFit="contain"
      />

      {/* 封面 + 播放按钮：未开始播放时盖在上面 */}
      {!started ? (
        <TouchableOpacity
          testID="video-poster"
          style={styles.posterLayer}
          activeOpacity={0.9}
          onPress={handlePlay}
          accessibilityRole="button"
          accessibilityLabel="播放视频"
        >
          {posterUri ? (
            <Image
              source={{ uri: posterUri }}
              style={styles.poster}
              contentFit="contain"
              transition={150}
              cachePolicy="memory-disk"
              onLoad={onLoad}
            />
          ) : (
            <View style={[styles.poster, styles.posterEmpty]} />
          )}
          <View style={styles.playBtn}>
            <Icon name="play" size={theme.icon.xl} color={theme.color.textOnMedia} />
          </View>
          <View style={styles.playHint}>
            <AppText variant="micro" color="textOnMedia">
              点击播放
            </AppText>
          </View>
        </TouchableOpacity>
      ) : null}
    </View>
  )
}

const makeStyles = (t: Theme) =>
  StyleSheet.create({
    container: {
      width: '100%',
      backgroundColor: t.color.videoBackdrop,
      overflow: 'hidden',
    },
    video: { width: '100%', height: '100%' },
    posterLayer: {
      ...StyleSheet.absoluteFillObject,
      alignItems: 'center',
      justifyContent: 'center',
    },
    poster: { ...StyleSheet.absoluteFillObject, width: '100%', height: '100%' },
    posterEmpty: { backgroundColor: t.color.videoBackdrop },
    playBtn: {
      width: 60,
      height: 60,
      borderRadius: t.radius.pill,
      backgroundColor: t.color.scrim,
      alignItems: 'center',
      justifyContent: 'center',
    },
    playHint: {
      position: 'absolute',
      bottom: t.space.md,
      paddingHorizontal: t.space.sm,
      paddingVertical: 2,
      borderRadius: t.radius.badge,
      backgroundColor: t.color.scrim,
    },
  })
