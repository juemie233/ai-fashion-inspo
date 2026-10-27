<script setup lang="ts">
/** 人脸注册候选弹窗（A3）：从「已匹配素材」或「未匹配候选脸簇」一键注册。
 *
 * 为什么需要它：全库注册率只有 4.4%，而注册入口要求上传照片或手挑素材——没人会为
 * 498 人做这件事。这里把现成的检测结果摊开：已匹配素材（她本人的脸，最可靠）+
 * TA 素材里反复出现但尚未匹配的脸（聚类，需要人确认）。
 *
 * **不做自动注册**：采集素材里有路人/买家秀/其他博主，误注册会污染整库匹配。
 * 上限 5 张与后端 `POST /{id}/face` 一致（平均池化用 1~5 张）。
 */

import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getFileUrl } from '@/api/inspirations'
import { formatCount } from '@/utils/format'
import type { PersonFaceCandidates } from '@/api/persons'

const props = defineProps<{
  visible: boolean
  loading: boolean
  registering: boolean
  candidates: PersonFaceCandidates | null
  personName: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'register', ids: string[]): void
}>()

/** 单次注册最多 5 张（后端平均池化的上限，超过会 400） */
const MAX_IMAGES = 5

const picked = ref<string[]>([])

/** 每次打开清空选择：上次勾的图不应该带进新一轮注册 */
watch(
  () => props.visible,
  (open) => {
    if (open) picked.value = []
  },
)

function toggle(id: string): void {
  if (picked.value.includes(id)) {
    picked.value = picked.value.filter((x) => x !== id)
    return
  }
  if (picked.value.length >= MAX_IMAGES) {
    Message.warning(`最多勾选 ${MAX_IMAGES} 张素材注册`)
    return
  }
  picked.value = [...picked.value, id]
}

/** 点整簇 = 用该簇里置信度最高的几张（后端已按 det_score 排序） */
function toggleCluster(ids: string[]): void {
  const already = ids.every((id) => picked.value.includes(id))
  if (already) {
    picked.value = picked.value.filter((x) => !ids.includes(x))
    return
  }
  const merged = [...new Set([...picked.value, ...ids])]
  if (merged.length > MAX_IMAGES) {
    Message.warning(`一个簇最多带 ${MAX_IMAGES} 张，请先取消部分勾选`)
    return
  }
  picked.value = merged
}

/** 一键用「已匹配素材」的前 3 张（A3a：最可靠的一路） */
function pickMatchedTop(): void {
  const ids = (props.candidates?.matched ?? []).slice(0, 3).map((m) => m.inspiration_id)
  if (!ids.length) {
    Message.info('该博主还没有已匹配到本人的人脸素材')
    return
  }
  picked.value = ids
}

const matched = computed(() => props.candidates?.matched ?? [])
const clusters = computed(() => props.candidates?.clusters ?? [])
const empty = computed(() => !props.loading && !matched.value.length && !clusters.value.length)

function clusterPicked(ids: string[]): boolean {
  return ids.every((id) => picked.value.includes(id))
}

function thumb(path: string | null | undefined): string {
  return path ? getFileUrl(path) : ''
}

function submit(): void {
  if (!picked.value.length) {
    Message.warning('请先勾选要用来注册的素材')
    return
  }
  emit('register', [...picked.value])
}
</script>

<template>
  <a-modal
    :visible="visible"
    :title="`人脸注册候选 · ${personName}`"
    :width="980"
    :footer="false"
    unmount-on-close
    @cancel="emit('update:visible', false)"
  >
    <a-alert type="info" class="fc-tip">
      勾选后会用这些素材里**检出的人脸**做平均池化特征注册（最多 {{ MAX_IMAGES }} 张）。
      素材里有路人、买家秀、其他博主，所以这里只给候选，**注册前请确认是本人**。
      <template v-if="candidates">
        · 已分析 {{ formatCount(candidates.candidate_faces) }} 张未匹配人脸
      </template>
    </a-alert>

    <a-spin :loading="loading" style="display: block">
      <!-- 已匹配素材：最可靠的一路（这些脸已经被确认是她） -->
      <div v-if="matched.length" class="fc-section">
        <div class="fc-head">
          <h4>已匹配到本人的人脸素材（{{ matched.length }}）</h4>
          <a-button size="mini" @click="pickMatchedTop">选前 3 张</a-button>
        </div>
        <div class="fc-grid">
          <div
            v-for="item in matched"
            :key="item.inspiration_id"
            class="fc-cell"
            :class="{ 'is-picked': picked.includes(item.inspiration_id) }"
            :title="`检测置信度 ${item.det_score}`"
            @click="toggle(item.inspiration_id)"
          >
            <img :src="thumb(item.thumbnail_path)" alt="" loading="lazy" />
            <span class="fc-score">{{ item.det_score.toFixed(2) }}</span>
          </div>
        </div>
      </div>

      <!-- 未匹配候选脸簇：TA 素材里反复出现的那张脸（可能是她本人，也可能是别人） -->
      <div v-if="clusters.length" class="fc-section">
        <div class="fc-head">
          <h4>素材里反复出现、尚未匹配的人脸（{{ clusters.length }} 簇）</h4>
          <span class="fc-hint">出现次数越多越可能是本人；整簇点击 = 用该簇前 3 张</span>
        </div>
        <div class="fc-clusters">
          <div
            v-for="cluster in clusters"
            :key="cluster.cluster_id"
            class="fc-cluster"
            :class="{ 'is-picked': clusterPicked(cluster.inspiration_ids) }"
            @click="toggleCluster(cluster.inspiration_ids)"
          >
            <div class="fc-cluster-main">
              <img :src="thumb(cluster.thumbnail_path)" alt="" loading="lazy" />
              <div class="fc-cluster-meta">
                <a-tag size="small" color="arcoblue">出现 {{ cluster.size }} 次</a-tag>
                <span class="fc-hint">平均置信度 {{ cluster.avg_score.toFixed(2) }}</span>
              </div>
            </div>
            <div class="fc-cluster-more">
              <img
                v-for="c in cluster.candidates.slice(0, 3)"
                :key="c.inspiration_id"
                :src="thumb(c.thumbnail_path)"
                alt=""
                loading="lazy"
              />
            </div>
          </div>
        </div>
      </div>

      <a-empty v-if="empty" description="没有可用的人脸候选：该博主还没有跑过人脸扫描" />
    </a-spin>

    <div class="fc-footer">
      <span class="fc-hint">已勾选 {{ picked.length }} / {{ MAX_IMAGES }} 张</span>
      <a-space>
        <a-button size="small" @click="emit('update:visible', false)">取消</a-button>
        <a-button
          type="primary"
          size="small"
          :loading="registering"
          :disabled="picked.length === 0"
          @click="submit"
        >
          用选中素材注册
        </a-button>
      </a-space>
    </div>
  </a-modal>
</template>

<style scoped>
.fc-tip {
  margin-bottom: 12px;
}
.fc-section {
  margin-bottom: 18px;
}
.fc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.fc-head h4 {
  margin: 0;
  font-size: 13px;
}
.fc-hint {
  color: var(--color-text-3);
  font-size: 12px;
}
.fc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(84px, 1fr));
  gap: 8px;
}
.fc-cell {
  position: relative;
  border: 2px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  background: var(--color-fill-2);
}
.fc-cell.is-picked {
  border-color: rgb(var(--primary-6));
}
.fc-cell img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
}
.fc-score {
  position: absolute;
  right: 3px;
  bottom: 3px;
  padding: 0 3px;
  border-radius: 3px;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 10px;
}
.fc-clusters {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}
.fc-cluster {
  display: flex;
  gap: 8px;
  padding: 8px;
  border: 2px solid var(--color-border-2);
  border-radius: 6px;
  cursor: pointer;
}
.fc-cluster.is-picked {
  border-color: rgb(var(--primary-6));
  background: var(--color-primary-light-1);
}
.fc-cluster-main img {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}
.fc-cluster-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 4px;
}
.fc-cluster-more {
  display: flex;
  gap: 4px;
  align-items: flex-start;
}
.fc-cluster-more img {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}
.fc-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-2);
}
</style>
