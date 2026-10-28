/** 视频浏览：播放器组件 + 素材库视频角标 + 详情页视频/图片分流。 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react-native'

jest.mock('expo-video', () => ({
  __esModule: true,
  // 播放器实例：只需要 play/pause，测试里断言是否被调用
  useVideoPlayer: jest.fn(() => ({ play: jest.fn(), pause: jest.fn() })),
  VideoView: ({ testID }: { testID?: string }) => {
    const { View } = require('react-native')
    return <View testID={testID ?? 'video-view'} />
  },
}))

jest.mock('../services/api', () => {
  const actual = jest.requireActual('../services/api')
  return {
    ...actual,
    apiClient: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
    addTagsToInspiration: jest.fn(),
    removeTagFromInspiration: jest.fn(),
    trashInspiration: jest.fn(),
    fetchSimilarInspirations: jest.fn(),
    fetchTagGroups: jest.fn(),
  }
})

import { useVideoPlayer } from 'expo-video'
import VideoPlayer from '../components/VideoPlayer'
import DetailScreen from '../app/detail/[id]'
import { useInspirationStore } from '../hooks/useInspirations'
import { findBareText } from '../test-utils/noBareText'
import { mockedApi } from '../test-utils/mockedApi'
// 用模块自身类型（而不是手写窄类型）：mock 新函数时不会再出现 TS2339
const api = mockedApi()

const VIDEO_DETAIL = {
  id: 'v1',
  source_type: 'douyin',
  source_url: null,
  source_author: null,
  file_path: 'images/2026-08/clip.mp4',
  thumbnail_path: 'thumbnails/2026-08/thumb_clip.jpg',
  media_type: 'video',
  is_favorite: false,
  created_at: '2026-08-01T00:00:00Z',
  tags: [],
}

const IMAGE_DETAIL = {
  ...VIDEO_DETAIL,
  id: 'i1',
  file_path: 'images/2026-08/pic.webp',
  thumbnail_path: 'thumbnails/2026-08/thumb_pic.jpg',
  media_type: 'image',
}

describe('VideoPlayer', () => {
  it('先显示封面帧与播放按钮，点击后才调用 play()', () => {
    const play = jest.fn()
    ;(useVideoPlayer as unknown as jest.Mock).mockReturnValue({ play, pause: jest.fn() })

    const utils = render(
      <VideoPlayer uri="http://h:18888/api/files/clip.mp4" posterUri="http://h/t.jpg" />,
    )

    // 展示播放器容器 + 封面层
    expect(screen.getByTestId('video-player')).toBeTruthy()
    expect(screen.getByTestId('video-poster')).toBeTruthy()
    expect(play).not.toHaveBeenCalled()

    fireEvent.press(screen.getByTestId('video-poster'))
    expect(play).toHaveBeenCalledTimes(1)
    // 播放后封面层隐藏
    expect(screen.queryByTestId('video-poster')).toBeNull()
    expect(findBareText(utils.toJSON())).toEqual([])
  })

  it('没有封面帧时也能渲染并播放', () => {
    const play = jest.fn()
    ;(useVideoPlayer as unknown as jest.Mock).mockReturnValue({ play, pause: jest.fn() })

    render(<VideoPlayer uri="http://h:18888/api/files/clip.mp4" />)

    expect(screen.getByTestId('video-poster')).toBeTruthy()
    fireEvent.press(screen.getByTestId('video-poster'))
    expect(play).toHaveBeenCalled()
  })
})

describe('详情页：视频 vs 图片', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.fetchSimilarInspirations.mockResolvedValue([])
    api.fetchTagGroups.mockResolvedValue([])
    useInspirationStore.setState({ apiBaseUrl: 'http://192.168.31.142:18888' })
  })

  it('视频素材渲染播放器（封面来自 thumbnail_path），且不出现「全屏」看图角标', async () => {
    api.apiClient.get.mockResolvedValue({ data: VIDEO_DETAIL })

    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByTestId('video-player')).toBeTruthy())

    expect(screen.getByTestId('video-poster')).toBeTruthy()
    expect(screen.queryByText('全屏')).toBeNull()
    // 播放器拿到的地址是视频文件本身，不是封面图
    const calls = (useVideoPlayer as unknown as jest.Mock).mock.calls
    expect(String(calls[calls.length - 1][0])).toContain('clip.mp4')
  })

  it('图片素材仍然渲染大图 + 全屏角标，不挂播放器', async () => {
    api.apiClient.get.mockResolvedValue({ data: IMAGE_DETAIL })

    render(<DetailScreen />)
    await waitFor(() => expect(screen.getByText('全屏')).toBeTruthy())

    expect(screen.queryByTestId('video-player')).toBeNull()
  })
})
