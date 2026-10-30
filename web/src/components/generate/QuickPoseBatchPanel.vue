<script setup lang="ts">
/** 一键出图（性感系 399 姿势轮播）。
 *
 * 用来「少点几下、连续出很多批」：维护一个姿势队列（初期固定为 ai-pose-gallery-zh 的
 * 「全身单人性感系」399 条），每次点一下就用**下一个没用过的姿势**出一批图，并支持
 * 「连续 N 次」自动接续下一批。用过的姿势记在 localStorage（换页/重启后继续），
 * 也能一键重置从头再来。
 *
 * 与参数面板分工：本组件只负责「选哪个姿势、出几张、多少步、连续几轮」，真正的提交
 * （拼 payload、切未审查档位、自动补描述）由父组件（GenerateParamForm）做——它才持有
 * 参考图/模型档位/描述这些状态。
 *
 * 排队实现：每次 emit('run') 后等父组件的 `busy`（提交中或正在生成）由 true 变回 false，
 * 判定这一轮结束再出下一轮；若 3 秒内 `busy` 没变成 true，说明这轮没启动（被校验拦住），
 * 立即停止并提示，避免队列空转。
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { fetchPoses, type PoseItem } from '@/api/poseGallery'

const props = withDefaults(
  defineProps<{
    /** ComfyUI 环境是否就绪 */
    platformReady: boolean
    /** 图生图（未审查版）档位是否就绪：没权重时不许一键出图 */
    modelReady: boolean
    /** 是否已选参考图（一键出图用当前参考图当主体） */
    hasReferences: boolean
    /** 正在提交或正在生成（父组件传 submitting || active） */
    busy: boolean
    /** 姿势体系（默认「全身单人性感系」；后续要扩其他体系时改这个 prop 即可） */
    system?: string
  }>(),
  { system: '全身单人・性感系' },
)

const emit = defineEmits<{
  /** 请求父组件用该姿势出一批图 */
  (
    e: 'run',
    payload: { pose: PoseItem; count: number; steps: number; round: number; rounds: number },
  ): void
  /** 连续出图结束（父组件可据此提示/刷新） */
  (e: 'batch-end', summary: { rounds: number; images: number }): void
}>()

/** 进度存储 key：按体系分开存，换体系不会互相污染。 */
const STORAGE_KEY = 'fashion-inspo:quickpose:used'

/** 每页拉多少：后端上限 100，399 条约 4 页。 */
const PAGE_SIZE = 100

// ── 控件（每次张数 / 步数 / 连续轮数）──
const count = ref(2)
const steps = ref(25)
const rounds = ref(1)

// ── 姿势队列 ──
const poses = ref<PoseItem[]>([])
const loading = ref(false)
const loadError = ref('')
const usedIds = ref<Set<string>>(new Set())

const total = computed(() => poses.value.length)
const usedCount = computed(() => usedIds.value.size)
const percent = computed(() =>
  total.value ? Math.round((usedCount.value / total.value) * 100) : 0,
)
/** 下一个没出过的姿势（按接口返回顺序轮播） */
const nextPose = computed<PoseItem | null>(
  () => poses.value.find((pose) => !usedIds.value.has(pose.id)) ?? null,
)

/** 连续次数的上限 = 剩余未出过的姿势数（想一次出完 399 个就填 399）。
 *
 * 为什么不再写死 20：这是个人批量出图的核心用法——一次把整个体系排完；写死小上限
 * 只会逼用户分几十次点。下限 1（全出完时需要先「重置进度」）。
 */
const maxRounds = computed(() => Math.max(1, total.value - usedCount.value))

/** rounds 超出剩余姿势数时夹回上限（出图后剩余变少会触发） */
watch(maxRounds, (max) => {
  if (rounds.value > max) rounds.value = max
})

/** 连续出图预计耗时（按每张约 40 秒粗估；Qwen 2.1 25 步 1024² 实测 20~45 秒/张） */
const estimateHint = computed(() => {
  const images = Math.max(1, count.value) * Math.max(1, rounds.value)
  const minutes = (images * 40) / 60
  return minutes >= 90
    ? `共 ${images} 张，约需 ${(minutes / 60).toFixed(1)} 小时`
    : `共 ${images} 张，约需 ${Math.max(1, Math.round(minutes))} 分钟`
})

// ── 连续出图状态机 ──
const queueActive = ref(false)
const queueDone = ref(0)
const queueTotal = ref(1)
/** 启动那一刻锁定的「每次张数 / 步数」：运行中改输入不影响已排队的轮次，汇总数字也才准 */
const queueCount = ref(2)
const queueSteps = ref(25)
/** 已 emit 一轮、正在等父组件跑完 */
const awaiting = ref(false)
/** 一轮发出后 3 秒内父组件还没进入 busy → 判定没启动，停止队列 */
let startGuard: ReturnType<typeof setTimeout> | null = null

function clearGuard(): void {
  if (startGuard) {
    clearTimeout(startGuard)
    startGuard = null
  }
}

function loadUsed(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const data = raw ? (JSON.parse(raw) as Record<string, unknown>) : {}
    const list = data?.[props.system]
    usedIds.value = new Set(Array.isArray(list) ? list.map(String) : [])
  } catch {
    usedIds.value = new Set()
  }
}

function saveUsed(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const data = raw ? (JSON.parse(raw) as Record<string, unknown>) : {}
    data[props.system] = [...usedIds.value]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch {
    // 隐私模式等场景写不了 localStorage：只影响「进度记忆」，不阻塞出图
  }
}

/** 拉全该体系的姿势（分页拉完；失败显示可重试的错误，不白屏）。 */
async function loadPoses(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const all: PoseItem[] = []
    let page = 1
    // 最多 20 页（2000 条）兜底，防止后端 total 异常时无限翻页
    while (page <= 20) {
      const data = await fetchPoses({ system: props.system, page, size: PAGE_SIZE })
      all.push(...data.items)
      if (data.items.length < PAGE_SIZE || all.length >= data.total) break
      page += 1
    }
    poses.value = all
    if (!all.length) loadError.value = '该体系没有可用姿势，稍后重试或换个体系'
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载姿势库失败'
  } finally {
    loading.value = false
  }
}

function markUsed(pose: PoseItem): void {
  usedIds.value = new Set([...usedIds.value, pose.id])
  saveUsed()
}

/** 当前是否有「连续出图」在跑 */
const running = computed(() => queueActive.value)

/** 不能一键出图的原因（空串=可以）；顺序按「先解决哪个」排。 */
const disabledReason = computed(() => {
  if (queueActive.value) return ''
  if (loading.value) return '正在加载姿势库…'
  if (loadError.value) return loadError.value
  if (!poses.value.length) return '姿势库为空，点「重试」重新加载'
  if (!nextPose.value) return '该体系姿势已全部出过，点「重置进度」可再来一轮'
  if (!props.hasReferences) return '先在上方选 1 张以上参考图（一键出图用当前参考图当主体）'
  if (!props.modelReady) return '图生图（未审查版）档位未就绪：请确认权重已放好并刷新环境'
  if (!props.platformReady) return 'ComfyUI 未就绪：先启动 ComfyUI 再点「刷新环境」'
  if (props.busy) return '正在生成上一批，结束后自动继续'
  return ''
})

const buttonText = computed(() => {
  const n = Math.max(1, rounds.value)
  return n > 1
    ? `一键出图（连续 ${n} 次，共 ${count.value * n} 张）`
    : `一键出图（${count.value} 张）`
})

/** 出下一轮。 */
function fireRound(): void {
  if (!queueActive.value) return
  if (props.busy) {
    awaiting.value = true
    return
  }
  const pose = nextPose.value
  if (!pose) {
    finishBatch('姿势已用完')
    return
  }
  markUsed(pose)
  queueDone.value += 1
  awaiting.value = true
  emit('run', {
    pose,
    count: queueCount.value,
    steps: queueSteps.value,
    round: queueDone.value,
    rounds: queueTotal.value,
  })
  clearGuard()
  // 一轮发出后若父组件 3 秒内没进入 busy，说明被校验拦住了（如档位突然未就绪/参考图被清空）
  startGuard = setTimeout(() => {
    startGuard = null
    if (queueActive.value && awaiting.value && !props.busy) {
      finishBatch('本轮未启动（检查参考图/档位是否被清空）')
    }
  }, 3000)
}

function fillAllRounds(): void {
  rounds.value = maxRounds.value
}

function startBatch(): void {
  if (running.value || disabledReason.value) return
  queueActive.value = true
  queueTotal.value = Math.min(Math.max(1, rounds.value), maxRounds.value)
  queueCount.value = count.value
  queueSteps.value = steps.value
  queueDone.value = 0
  awaiting.value = false
  Message.info(`开始一键出图：${queueTotal.value} 轮 × ${count.value} 张`)
  fireRound()
}

/** 结束队列（正常完成或异常停止）。 */
function finishBatch(reason = ''): void {
  if (!queueActive.value) return
  const done = queueDone.value
  const images = done * queueCount.value
  queueActive.value = false
  awaiting.value = false
  clearGuard()
  if (reason) Message.warning(reason)
  else Message.success(`一键出图完成：${done} 轮 / ${images} 张，结果在候选区与生成历史`)
  emit('batch-end', { rounds: done, images })
}

function stopBatch(): void {
  if (!queueActive.value) return
  clearGuard()
  const done = queueDone.value
  const images = done * queueCount.value
  queueActive.value = false
  awaiting.value = false
  Message.info(`已停止连续出图（本次已出 ${done} 轮 / ${images} 张）`)
  emit('batch-end', { rounds: done, images })
}

function resetProgress(): void {
  if (running.value) return
  usedIds.value = new Set()
  saveUsed()
  Message.success('已重置姿势进度，从头开始')
}

// 父组件的 busy 由 true→false = 这一轮结束 → 继续下一轮或收尾
watch(
  () => props.busy,
  (busy) => {
    if (busy) {
      // 真跑起来了：清掉「没启动」的守卫
      clearGuard()
      return
    }
    if (!queueActive.value || !awaiting.value) return
    awaiting.value = false
    if (queueDone.value >= queueTotal.value) {
      finishBatch()
      return
    }
    // 留 600ms 给界面刷新（候选区/轮询落地），再出下一轮
    startGuard = setTimeout(() => {
      startGuard = null
      if (queueActive.value && !awaiting.value) fireRound()
    }, 600)
  },
)

onMounted(() => {
  loadUsed()
  void loadPoses()
})

onBeforeUnmount(() => {
  clearGuard()
  queueActive.value = false
})
</script>

<template>
  <div class="quick-pose">
    <div class="quick-head">
      <span class="gen-label">一键出图</span>
      <a-tag size="small" color="arcoblue">{{ system }}</a-tag>
      <span class="gen-hint">按姿势库顺序轮播，每次自动换下一个姿势</span>
    </div>

    <div class="quick-body">
      <!-- 下一个姿势预览：点之前就知道会出什么姿势 -->
      <div v-if="nextPose" class="quick-next">
        <img :src="nextPose.thumb_url" :alt="nextPose.name" loading="lazy" />
        <div class="quick-next-meta">
          <div class="quick-next-name">{{ nextPose.name }} · {{ nextPose.angle }}</div>
          <div class="gen-hint">
            {{ nextPose.pose_no }} · {{ nextPose.category }} · 已用 {{ usedCount }}/{{ total }}
          </div>
        </div>
      </div>
      <div v-else class="quick-next quick-next--empty">
        <span class="gen-hint">{{
          loading ? '正在加载姿势库…' : loadError || '暂无可用姿势'
        }}</span>
      </div>

      <!-- 每次张数 / 步数 / 连续轮数：三个数就够了，不多加输入 -->
      <div class="quick-controls">
        <span class="quick-control">
          <a-input-number
            v-model="count"
            :min="1"
            :max="4"
            :disabled="running"
            size="small"
            style="width: 104px"
          >
            <template #prefix>每次</template>
          </a-input-number>
          <span class="gen-hint">张</span>
        </span>
        <span class="quick-control">
          <a-input-number
            v-model="steps"
            :min="1"
            :max="30"
            :disabled="running"
            size="small"
            style="width: 104px"
          >
            <template #prefix>步数</template>
          </a-input-number>
          <span class="gen-hint">步</span>
        </span>
        <span class="quick-control">
          <a-input-number
            v-model="rounds"
            :min="1"
            :max="maxRounds"
            :disabled="running"
            size="small"
            style="width: 118px"
          >
            <template #prefix>连续</template>
          </a-input-number>
          <span class="gen-hint">次</span>
        </span>
        <a-button
          type="text"
          size="mini"
          :disabled="running || maxRounds <= 1 || rounds >= maxRounds"
          @click="fillAllRounds"
        >
          设为剩余全部（{{ maxRounds }}）
        </a-button>
      </div>
    </div>

    <a-progress :percent="percent / 100" :show-text="false" size="small" class="quick-progress" />

    <div class="quick-actions">
      <a-button
        type="primary"
        :disabled="!!disabledReason || running"
        :loading="running"
        @click="startBatch"
      >
        {{ buttonText }}
      </a-button>
      <a-button v-if="running" status="warning" @click="stopBatch">停止连续出图</a-button>
      <a-button type="text" size="mini" :disabled="running || !usedCount" @click="resetProgress">
        重置进度
      </a-button>
      <span v-if="running" class="gen-hint">
        第 {{ queueDone }}/{{ queueTotal }} 轮 · 结束后自动出下一轮
      </span>
      <span v-else-if="disabledReason" class="gen-hint">{{ disabledReason }}</span>
      <span v-else class="gen-hint">
        每次用一个未出过的姿势；进度会记住（{{ usedCount }}/{{ total }}）
        <template v-if="rounds > 1"> · {{ estimateHint }}（按每张约 40 秒估算）</template>
        <template v-if="rounds >= 20"> · 连续出图期间请保持本页打开（队列在前端运行）</template>
      </span>
    </div>
  </div>
</template>

<style scoped>
.quick-pose {
  margin: 6px 0 12px;
  padding: 10px;
  border: 1px dashed var(--color-border-2);
  border-radius: 8px;
  background: var(--color-fill-1);
}

.quick-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.quick-head .gen-label {
  margin: 0;
}

.quick-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.quick-next {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 220px;
}

.quick-next--empty {
  min-height: 56px;
}

.quick-next img {
  width: 42px;
  height: 56px;
  object-fit: cover;
  border-radius: 4px;
  display: block;
  background: var(--color-fill-2);
}

.quick-next-name {
  color: var(--color-text-1);
  font-size: 13px;
  font-weight: 600;
}

.quick-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  margin-left: auto;
}

/* 输入框与单位绑成一组：窗口变窄时整组换行，不会出现「张/步/次」被单独折到下一行 */
.quick-control {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.quick-progress {
  margin: 8px 0 0;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
