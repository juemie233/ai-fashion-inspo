<script setup lang="ts">
/** 疑似重复标签「图片对比」弹窗：左右各展示一个标签对应的素材图，
 * 支持随机切换对比、合并到 A/B、就地重命名标签。
 *
 * 随机显示按钮在「A、B 各自都只有 1 张素材」时禁用（没有可随机的余地）。
 */

import { computed, ref, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getApiErrorMessage } from '@/utils/apiError'
import { getFileUrl } from '@/api/inspirations'
import { fetchTagInspirations, mergeTags, updateTag, type TagInspiration } from '@/api/tags'
import type { DuplicateIssuePair } from '@/types/tagAdvanced'

const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ changed: [] }>()

const props = defineProps<{
  pair: DuplicateIssuePair | null
}>()

/** 单侧标签的素材池与当前展示索引 */
interface SideState {
  items: TagInspiration[]
  index: number
  loading: boolean
  total: number
}

function emptySide(): SideState {
  return { items: [], index: 0, loading: false, total: 0 }
}

const sideA = ref<SideState>(emptySide())
const sideB = ref<SideState>(emptySide())
const busy = ref(false)

/** 就地重命名状态 */
const renaming = ref<'a' | 'b' | null>(null)
const renameValue = ref('')
const savingRename = ref(false)

watch(visible, async (v) => {
  if (v && props.pair?.tag_a && props.pair.tag_b) {
    sideA.value = emptySide()
    sideB.value = emptySide()
    renaming.value = null
    await Promise.all([loadSide('a', props.pair.tag_a.id), loadSide('b', props.pair.tag_b.id)])
  }
})

/** 拉取某标签的素材（图片优先；size 上限 200，足够做随机池） */
async function loadSide(side: 'a' | 'b', tagId: number) {
  const state = side === 'a' ? sideA.value : sideB.value
  state.loading = true
  try {
    const data = await fetchTagInspirations(tagId, 1, 200)
    // 仅保留可展示的图片素材（排除视频），无图时仍保留列表以显示空态
    const imageItems = (data.items ?? []).filter(
      (it: TagInspiration) => it.media_type !== 'video' && (it.thumbnail_path || it.file_path),
    )
    state.items = imageItems
    state.total = data.total ?? imageItems.length
    state.index = 0
  } catch (e) {
    Message.error(getApiErrorMessage(e, '加载标签素材失败'))
  } finally {
    state.loading = false
  }
}

const currentA = computed(() => sideA.value.items[sideA.value.index] ?? null)
const currentB = computed(() => sideB.value.items[sideB.value.index] ?? null)

function imgSrc(item: TagInspiration | null): string {
  if (!item) return ''
  return getFileUrl(item.thumbnail_path || item.file_path)
}

/** 随机显示：A/B 两侧各自独立随机抽一张；当两侧都只有 ≤1 张（含各 1 张或无图）时
 *  没有可随机的余地，按钮禁用。 */
const canRandom = computed(
  () => sideA.value.items.length > 1 || sideB.value.items.length > 1,
)

function randomPick() {
  if (!canRandom.value) return
  const aLen = sideA.value.items.length
  const bLen = sideB.value.items.length
  sideA.value.index = aLen > 1 ? Math.floor(Math.random() * aLen) : 0
  sideB.value.index = bLen > 1 ? Math.floor(Math.random() * bLen) : 0
}

function startRename(side: 'a' | 'b') {
  const tag = side === 'a' ? props.pair?.tag_a : props.pair?.tag_b
  if (!tag) return
  renaming.value = side
  renameValue.value = tag.name
}

function cancelRename() {
  renaming.value = null
  renameValue.value = ''
}

async function saveRename() {
  const side = renaming.value
  const tag = side === 'a' ? props.pair?.tag_a : side === 'b' ? props.pair?.tag_b : null
  if (!side || !tag) return
  const name = renameValue.value.trim()
  if (!name) {
    Message.warning('标签名不能为空')
    return
  }
  if (name === tag.name) {
    cancelRename()
    return
  }
  savingRename.value = true
  try {
    await updateTag(tag.id, { name })
    tag.name = name
    Message.success('已重命名')
    renaming.value = null
    renameValue.value = ''
    emit('changed')
  } catch (e) {
    Message.error(getApiErrorMessage(e, '重命名失败'))
  } finally {
    savingRename.value = false
  }
}

/** 合并：target 保留，source 被合并 */
function doMerge(target: 'tag_a' | 'tag_b') {
  const source = target === 'tag_a' ? props.pair!.tag_b : props.pair!.tag_a
  const dest = props.pair![target]
  if (!source || !dest) return
  Modal.confirm({
    title: '确认合并',
    content: `将「${source.name}」合并到「${dest.name}」？合并后素材归入后者，前者删除且不可恢复。`,
    onOk: async () => {
      busy.value = true
      try {
        await mergeTags(source.id, dest.id)
        Message.success('合并完成')
        visible.value = false
        emit('changed')
      } catch (e) {
        Message.error(getApiErrorMessage(e, '合并失败'))
      } finally {
        busy.value = false
      }
    },
  })
}
</script>

<template>
  <a-modal
    v-model:visible="visible"
    title="标签图片对比"
    :width="860"
    :footer="false"
    :mask-closable="!busy"
  >
    <div v-if="pair" class="cmp">
      <div class="cmp-similarity">相似度 {{ (pair.similarity * 100).toFixed(0) }}%</div>

      <div class="cmp-grid">
        <!-- 标签 A -->
        <div class="cmp-col">
          <div class="cmp-col-head">
            <template v-if="renaming === 'a'">
              <a-input
                v-model="renameValue"
                size="small"
                :max-length="50"
                @press-enter="saveRename"
                @keyup.esc="cancelRename"
              />
              <a-button size="mini" type="text" :loading="savingRename" @click="saveRename">
                保存
              </a-button>
              <a-button size="mini" type="text" @click="cancelRename">取消</a-button>
            </template>
            <template v-else>
              <span class="cmp-tag-name" title="标签 A">A · {{ pair.tag_a?.name }}</span>
              <a-button size="mini" type="text" @click="startRename('a')">改名</a-button>
            </template>
          </div>
          <div class="cmp-img-box">
            <a-spin :loading="sideA.loading" class="cmp-spin">
              <a-image
                v-if="currentA"
                :src="imgSrc(currentA)"
                :width="'100%'"
                fit="contain"
              />
              <div v-else class="cmp-empty">该标签无图片素材</div>
            </a-spin>
          </div>
          <div class="cmp-count">{{ sideA.total }} 个素材</div>
        </div>

        <!-- 标签 B -->
        <div class="cmp-col">
          <div class="cmp-col-head">
            <template v-if="renaming === 'b'">
              <a-input
                v-model="renameValue"
                size="small"
                :max-length="50"
                @press-enter="saveRename"
                @keyup.esc="cancelRename"
              />
              <a-button size="mini" type="text" :loading="savingRename" @click="saveRename">
                保存
              </a-button>
              <a-button size="mini" type="text" @click="cancelRename">取消</a-button>
            </template>
            <template v-else>
              <span class="cmp-tag-name" title="标签 B">B · {{ pair.tag_b?.name }}</span>
              <a-button size="mini" type="text" @click="startRename('b')">改名</a-button>
            </template>
          </div>
          <div class="cmp-img-box">
            <a-spin :loading="sideB.loading" class="cmp-spin">
              <a-image
                v-if="currentB"
                :src="imgSrc(currentB)"
                :width="'100%'"
                fit="contain"
              />
              <div v-else class="cmp-empty">该标签无图片素材</div>
            </a-spin>
          </div>
          <div class="cmp-count">{{ sideB.total }} 个素材</div>
        </div>
      </div>

      <div class="cmp-actions">
        <a-space>
          <a-button @click="randomPick" :disabled="!canRandom || busy">
            🎲 随机显示
          </a-button>
        </a-space>
        <a-space>
          <a-button status="warning" :disabled="busy" @click="doMerge('tag_a')">
            合并到 A
          </a-button>
          <a-button status="warning" :disabled="busy" @click="doMerge('tag_b')">
            合并到 B
          </a-button>
        </a-space>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.cmp {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cmp-similarity {
  font-size: 13px;
  color: #6b7280;
  text-align: center;
}
.cmp-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.cmp-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cmp-col-head {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.cmp-tag-name {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cmp-img-box {
  height: 320px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f9fafb;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.cmp-spin {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.cmp-empty {
  color: #9ca3af;
  font-size: 13px;
}
.cmp-count {
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}
.cmp-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  margin-top: 4px;
}
</style>
